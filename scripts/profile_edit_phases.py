from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import threading
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from dataclasses import FrozenInstanceError

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_benchmark_module():
    spec = importlib.util.spec_from_file_location("run_pg_benchmark", REPO_ROOT / "scripts" / "run_pg_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_pg_benchmark"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            "count": 0,
            "mean_ms": 0.0,
            "median_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "max_ms": 0.0,
        }
    return {
        "count": len(values),
        "mean_ms": statistics.fmean(values),
        "median_ms": statistics.median(values),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
        "max_ms": max(values),
    }


@dataclass
class TimedEvent:
    name: str
    started_at: float
    ended_at: float
    duration_ms: float
    thread_name: str


class EventRecorder:
    def __init__(self) -> None:
        self._events: list[TimedEvent] = []
        self._lock = threading.Lock()

    def record(self, name: str, started_at: float, ended_at: float) -> None:
        with self._lock:
            self._events.append(
                TimedEvent(
                    name=name,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=(ended_at - started_at) * 1000.0,
                    thread_name=threading.current_thread().name,
                )
            )

    def events_in_window(self, started_at: float, ended_at: float) -> list[TimedEvent]:
        with self._lock:
            return [
                event
                for event in self._events
                if event.started_at >= started_at and event.ended_at <= ended_at
            ]

    def all_events(self) -> list[TimedEvent]:
        with self._lock:
            return list(self._events)


def thread_role(thread_name: str) -> str:
    lowered = thread_name.lower()
    if "checkpoint-worker" in lowered:
        return "checkpoint_worker"
    if "command-log-worker" in lowered:
        return "command_log_worker"
    if lowered == "mainthread":
        return "main"
    return "other"


class TimedLock:
    def __init__(self, inner: Any, recorder: EventRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def acquire(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return self._inner.acquire(*args, **kwargs)
        finally:
            self._recorder.record("redis.lock.acquire", started, time.perf_counter())

    def release(self, *args, **kwargs):
        return self._inner.release(*args, **kwargs)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


class PatchManager:
    def __init__(self) -> None:
        self._restore: list[Callable[[], None]] = []

    def set_attr(self, obj: Any, name: str, value: Any) -> None:
        original = getattr(obj, name)
        try:
            setattr(obj, name, value)
            self._restore.append(lambda: setattr(obj, name, original))
        except FrozenInstanceError:
            object.__setattr__(obj, name, value)
            self._restore.append(lambda: object.__setattr__(obj, name, original))

    def restore_all(self) -> None:
        while self._restore:
            restore = self._restore.pop()
            restore()

    def add_restore(self, fn: Callable[[], None]) -> None:
        self._restore.append(fn)


def _caller_suffix() -> str:
    for frame in reversed(traceback.extract_stack(limit=30)):
        path = frame.filename.replace("\\", "/")
        if "/iruka_vfs/" not in path:
            continue
        if path.endswith("/scripts/profile_edit_phases.py"):
            continue
        return f"{Path(path).name}:{frame.name}:{frame.lineno}"
    return "unknown"


def timed_wrapper(
    recorder: EventRecorder,
    name: str,
    fn: Callable[..., Any],
    *,
    annotate_caller: bool = False,
) -> Callable[..., Any]:
    def wrapped(*args, **kwargs):
        started = time.perf_counter()
        event_name = name
        if annotate_caller:
            event_name = f"{name}:{_caller_suffix()}"
        try:
            return fn(*args, **kwargs)
        finally:
            recorder.record(event_name, started, time.perf_counter())

    return wrapped


def install_tracing(recorder: EventRecorder, scenario: str) -> PatchManager:
    from iruka_vfs import service
    from iruka_vfs import workspace_mirror

    patches = PatchManager()
    client = service._get_redis_client()

    for target, attr, label in [
        (service, "run_virtual_bash", "service.run_virtual_bash"),
        (service, "ensure_virtual_workspace", "service.ensure_virtual_workspace"),
        (service, "_ensure_async_log_worker", "service.ensure_async_log_worker"),
        (service, "_ensure_workspace_checkpoint_worker_api", "service.ensure_workspace_checkpoint_worker"),
        (service, "_ensure_mem_cache_worker_api", "service.ensure_mem_cache_worker"),
        (service, "_run_command_chain", "service.run_command_chain"),
        (service, "_exec_edit", "service.exec_edit"),
        (service, "_write_file", "service.write_file"),
        (service, "_set_workspace_mirror_api", "service.set_workspace_mirror"),
        (service, "_prepare_artifacts_for_log", "service.prepare_log_artifacts"),
        (workspace_mirror, "set_workspace_mirror", "mirror.set_workspace_mirror"),
        (workspace_mirror, "enqueue_workspace_checkpoint", "mirror.enqueue_checkpoint"),
        (workspace_mirror, "load_workspace_mirror_by_base_key", "mirror.load_workspace_mirror"),
        (workspace_mirror, "flush_workspace_mirror", "mirror.flush_workspace_mirror"),
        (service._repositories.node, "update_node_content", "repo.update_node_content"),
        (service._repositories.node, "create_node", "repo.create_node"),
        (service._repositories.command_log, "create_command_log", "repo.create_command_log"),
        (service._repositories.command_log, "bulk_insert_command_logs", "repo.bulk_insert_command_logs"),
        (Session, "execute", "sqlalchemy.Session.execute"),
        (Session, "scalars", "sqlalchemy.Session.scalars"),
        (Session, "commit", "sqlalchemy.Session.commit"),
        (Session, "flush", "sqlalchemy.Session.flush"),
    ]:
        patches.set_attr(
            target,
            attr,
            timed_wrapper(
                recorder,
                label,
                getattr(target, attr),
                annotate_caller=label.startswith("sqlalchemy.Session."),
            ),
        )

    for method_name in ["get", "set", "delete", "sadd", "srem", "rpush", "blpop"]:
        if hasattr(client, method_name):
            patches.set_attr(client, method_name, timed_wrapper(recorder, f"redis.{method_name}", getattr(client, method_name)))

    if hasattr(client, "lock"):
        original_lock = client.lock

        def wrapped_lock(*args, **kwargs):
            return TimedLock(original_lock(*args, **kwargs), recorder)

        patches.set_attr(client, "lock", wrapped_lock)

    if scenario == "no_checkpoint":
        patches.set_attr(service, "_ensure_workspace_checkpoint_worker_api", lambda engine: None)
        patches.set_attr(service, "_enqueue_workspace_checkpoint", lambda base_key: None)
        patches.set_attr(workspace_mirror, "enqueue_workspace_checkpoint", lambda base_key: None)
    elif scenario == "no_log_write":
        patches.set_attr(service, "ASYNC_COMMAND_LOGGING", False)
        original_command_log = service._repositories.command_log
        noop_log = SimpleNamespace(
            create_command_log=lambda db, payload: 0,
            bulk_insert_command_logs=lambda db, payloads: None,
        )
        object.__setattr__(service._repositories, "command_log", noop_log)
        patches.add_restore(lambda: object.__setattr__(service._repositories, "command_log", original_command_log))

    return patches


def aggregate_events(events: list[TimedEvent]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for event in events:
        buckets[(thread_role(event.thread_name), event.name)].append(event.duration_ms)
    rows: list[dict[str, Any]] = []
    for (role, name), durations in sorted(buckets.items(), key=lambda item: statistics.fmean(item[1]), reverse=True):
        rows.append(
            {
                "role": role,
                "phase": name,
                "count": len(durations),
                "mean_ms": statistics.fmean(durations),
                "p95_ms": percentile(durations, 0.95),
                "max_ms": max(durations),
                "total_ms": sum(durations),
            }
        )
    return rows


def wait_for_checkpoint(module, bench_workspace: Any, target_revision: int, timeout_seconds: float) -> dict[str, Any]:
    from iruka_vfs import service

    started = time.perf_counter()
    while (time.perf_counter() - started) < timeout_seconds:
        mirror = service._get_workspace_mirror_api(
            bench_workspace.workspace_id,
            tenant_key=bench_workspace.tenant_id,
        )
        if mirror and int(mirror.checkpoint_revision) >= target_revision:
            return {
                "caught_up": True,
                "wait_ms": (time.perf_counter() - started) * 1000.0,
                "checkpoint_revision": int(mirror.checkpoint_revision),
            }
        time.sleep(0.01)
    mirror = service._get_workspace_mirror_api(
        bench_workspace.workspace_id,
        tenant_key=bench_workspace.tenant_id,
    )
    return {
        "caught_up": False,
        "wait_ms": (time.perf_counter() - started) * 1000.0,
        "checkpoint_revision": int(mirror.checkpoint_revision) if mirror else 0,
    }


def run_scenario(
    module,
    session_local,
    *,
    scenario: str,
    tenant: str,
    iterations: int,
    chapter_bytes: int,
    checkpoint_timeout_seconds: float,
) -> dict[str, Any]:
    from iruka_vfs import service

    recorder = EventRecorder()
    patches = install_tracing(recorder, scenario)
    module.cleanup_benchmark_data(session_local, tenant + "%")
    bench_workspace = module.prepare_workspace(
        session_local,
        tenant_id=tenant,
        runtime_key=f"{tenant}:phase-profile",
        file_index=1,
        chapter_text=module.render_size_target(chapter_bytes, marker_count=max(iterations + 8, 256)),
        context_files={"outline.md": "benchmark marker\n" * 8},
        skill_files={"style.md": "keep edits deterministic\n"},
    )

    chapter_path = "/workspace/chapters/chapter_1.md"
    command_latencies: list[float] = []
    checkpoint_waits: list[float] = []
    iteration_rows: list[dict[str, Any]] = []

    try:
        for i in range(iterations):
            command = f"edit {chapter_path} --find MARKER_{i:03d} --replace MARKER_{i + 1:03d}"
            with session_local() as db:
                started = time.perf_counter()
                result = bench_workspace.handle.bash(db, command)
                returned = time.perf_counter()
                if result["exit_code"] != 0:
                    raise RuntimeError(f"{scenario} edit failed: {result['stderr']}")
            mirror = service._get_workspace_mirror_api(
                bench_workspace.workspace_id,
                tenant_key=bench_workspace.tenant_id,
            )
            target_revision = int(mirror.revision) if mirror else 0
            catchup = {"caught_up": True, "wait_ms": 0.0, "checkpoint_revision": int(mirror.checkpoint_revision) if mirror else 0}
            if scenario != "no_checkpoint" and mirror and int(mirror.checkpoint_revision) < target_revision:
                catchup = wait_for_checkpoint(module, bench_workspace, target_revision, checkpoint_timeout_seconds)
            finished = time.perf_counter()

            command_events = recorder.events_in_window(started, returned)
            post_events = recorder.events_in_window(returned, finished)
            command_ms = (returned - started) * 1000.0
            command_latencies.append(command_ms)
            checkpoint_waits.append(float(catchup["wait_ms"]))
            iteration_rows.append(
                {
                    "iteration": i,
                    "command": command,
                    "command_ms": command_ms,
                    "checkpoint_wait_ms": float(catchup["wait_ms"]),
                    "checkpoint_caught_up": bool(catchup["caught_up"]),
                    "target_revision": target_revision,
                    "checkpoint_revision": int(catchup["checkpoint_revision"]),
                    "command_phases": aggregate_events(command_events),
                    "post_return_phases": aggregate_events(post_events),
                }
            )
        all_events = recorder.all_events()
        return {
            "scenario": scenario,
            "iterations": iterations,
            "command_summary": summarize(command_latencies),
            "checkpoint_wait_summary": summarize(checkpoint_waits),
            "top_phases_overall": aggregate_events(all_events)[:20],
            "top_phases_main_thread": [row for row in aggregate_events(all_events) if row["role"] == "main"][:20],
            "top_phases_checkpoint_worker": [row for row in aggregate_events(all_events) if row["role"] == "checkpoint_worker"][:20],
            "iterations_detail": iteration_rows,
        }
    finally:
        patches.restore_all()
        time.sleep(0.25)
        module.cleanup_benchmark_data(session_local, tenant + "%")


def markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Edit Phase Profile Report",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Database host: `{payload['database']['host']}`",
        f"- Database name: `{payload['database']['database']}`",
        "",
    ]
    for scenario in payload["scenarios"]:
        cmd = scenario["command_summary"]
        wait = scenario["checkpoint_wait_summary"]
        lines.extend(
            [
                f"## Scenario `{scenario['scenario']}`",
                "",
                f"- Command mean/p95: `{cmd['mean_ms']:.2f} / {cmd['p95_ms']:.2f} ms`",
                f"- Checkpoint catch-up mean/p95: `{wait['mean_ms']:.2f} / {wait['p95_ms']:.2f} ms`",
                "",
                "| Role | Phase | Count | Mean (ms) | P95 (ms) | Max (ms) | Total (ms) |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in scenario["top_phases_overall"][:12]:
            lines.append(
                f"| `{row['role']}` | `{row['phase']}` | {row['count']} | {row['mean_ms']:.2f} | {row['p95_ms']:.2f} | {row['max_ms']:.2f} | {row['total_ms']:.2f} |"
            )
        lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile edit command phases for iruka_vfs.")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--scenario", default="baseline", choices=["baseline", "no_checkpoint", "no_log_write"])
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--chapter-bytes", type=int, default=64 * 1024)
    parser.add_argument("--checkpoint-timeout-seconds", type=float, default=3.0)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--markdown-out", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    module = load_benchmark_module()
    db_url = args.db_url or module.default_db_url()
    module.configure_dependencies(db_url)
    engine = create_engine(db_url, future=True, pool_pre_ping=True)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "database": {
            "host": db_url.split("@", 1)[-1].split(":", 1)[0].split("/", 1)[0],
            "database": db_url.rsplit("/", 1)[-1],
        },
        "scenarios": [],
    }
    payload["scenarios"].append(
        run_scenario(
            module,
            session_local,
            scenario=args.scenario,
            tenant=f"phase_profile_{args.scenario}_{stamp}",
            iterations=args.iterations,
            chapter_bytes=args.chapter_bytes,
            checkpoint_timeout_seconds=args.checkpoint_timeout_seconds,
        )
    )

    suffix = f"{args.scenario}_{stamp}"
    json_path = Path(args.json_out) if args.json_out else REPO_ROOT / "benchmark_reports" / f"edit_phase_profile_{suffix}.json"
    md_path = Path(args.markdown_out) if args.markdown_out else REPO_ROOT / "benchmark_reports" / f"edit_phase_profile_{suffix}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(payload) + "\n", encoding="utf-8")
    print(json.dumps({"json_report": str(json_path), "markdown_report": str(md_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
