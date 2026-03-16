from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import json
import os
import statistics
import sys
import threading
import time
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from iruka_vfs import WritableFileSource, create_workspace
from iruka_vfs.dependencies import VFSDependencies, configure_vfs_dependencies


class Base(DeclarativeBase):
    pass


class BenchmarkWorkspaceModel(Base):
    __tablename__ = "agent_workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    chapter_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    current_objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    focus_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="benchmark")


class BenchmarkFileNodeModel(Base):
    __tablename__ = "virtual_file_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="benchmark")
    workspace_id: Mapped[int] = mapped_column(ForeignKey("agent_workspaces.id"), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("virtual_file_nodes.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    node_type: Mapped[str] = mapped_column(String(16), nullable=False, default="file")
    content_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BenchmarkShellSessionModel(Base):
    __tablename__ = "virtual_shell_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="benchmark")
    workspace_id: Mapped[int] = mapped_column(ForeignKey("agent_workspaces.id"), nullable=False)
    cwd_node_id: Mapped[int | None] = mapped_column(ForeignKey("virtual_file_nodes.id"), nullable=True)
    env_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BenchmarkShellCommandModel(Base):
    __tablename__ = "virtual_shell_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="benchmark")
    session_id: Mapped[int] = mapped_column(ForeignKey("virtual_shell_sessions.id"), nullable=False)
    raw_cmd: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parsed_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stdout_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artifacts_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BenchmarkPatchModel(Base):
    __tablename__ = "virtual_patches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="benchmark")
    workspace_id: Mapped[int] = mapped_column(ForeignKey("agent_workspaces.id"), nullable=False)
    file_node_id: Mapped[int] = mapped_column(ForeignKey("virtual_file_nodes.id"), nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    patch_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="applied")
    conflict_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    applied_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


@dataclass
class BenchSettings:
    default_tenant_id: str = "benchmark"
    redis_key_namespace: str = "iruka-vfs-benchmark"
    redis_url: str = "memory://"
    database_url: str = ""


class BenchChapter:
    pass


@dataclass
class InMemoryLock:
    _lock: threading.Lock

    def acquire(self, blocking: bool = True, blocking_timeout: float | None = None) -> bool:
        timeout = -1 if blocking_timeout is None else blocking_timeout
        return self._lock.acquire(blocking=blocking, timeout=timeout)

    def release(self) -> None:
        if self._lock.locked():
            self._lock.release()


@dataclass
class InMemoryRedis:
    store: dict[str, Any] = field(default_factory=dict)
    sets: dict[str, set[str]] = field(default_factory=dict)
    queues: dict[str, deque[str]] = field(default_factory=dict)
    locks: dict[str, threading.Lock] = field(default_factory=dict)
    condition: threading.Condition = field(default_factory=threading.Condition)

    def get(self, key: str) -> Any:
        return self.store.get(key)

    def set(self, key: str, value: Any) -> None:
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.store.pop(key, None)
        self.sets.pop(key, None)

    def sadd(self, key: str, value: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.add(value)
        return 1 if len(bucket) != before else 0

    def srem(self, key: str, value: str) -> int:
        bucket = self.sets.setdefault(key, set())
        if value in bucket:
            bucket.remove(value)
            return 1
        return 0

    def rpush(self, key: str, value: str) -> None:
        with self.condition:
            self.queues.setdefault(key, deque()).append(value)
            self.condition.notify_all()

    def blpop(self, key: str, timeout: int = 1) -> tuple[str, str] | None:
        deadline = time.time() + timeout
        with self.condition:
            while True:
                queue = self.queues.setdefault(key, deque())
                if queue:
                    return key, queue.popleft()
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self.condition.wait(timeout=remaining)

    def scard(self, key: str) -> int:
        return len(self.sets.get(key, set()))

    def llen(self, key: str) -> int:
        return len(self.queues.get(key, deque()))

    def lock(self, key: str, timeout: int = 30, blocking_timeout: int = 5) -> InMemoryLock:
        return InMemoryLock(self.locks.setdefault(key, threading.Lock()))


@dataclass
class MutableText:
    value: str
    lock: threading.Lock = field(default_factory=threading.Lock)

    def read(self) -> str:
        with self.lock:
            return self.value

    def write(self, text_value: str) -> None:
        with self.lock:
            self.value = text_value


@dataclass(frozen=True)
class BenchmarkWorkspace:
    tenant_id: str
    workspace_id: int
    virtual_chapter_id: int
    workspace: Any
    handle: Any
    chapter_store: MutableText


def utc_now() -> datetime:
    return datetime.now(UTC)


def default_db_url() -> str:
    return (
        "postgresql+psycopg://file_user:Buyaole88@"
        "pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com:5432/file_sys"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run iruka_vfs PostgreSQL benchmark and generate a report.")
    parser.add_argument("--db-url", default=os.getenv("DATABASE_URL") or default_db_url())
    parser.add_argument("--workspace-count", type=int, default=8)
    parser.add_argument("--commands-per-workspace", type=int, default=24)
    parser.add_argument("--latency-iterations", type=int, default=30)
    parser.add_argument("--warmup-iterations", type=int, default=3)
    parser.add_argument("--chapter-bytes", type=int, default=64 * 1024)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--markdown-out", default="")
    parser.add_argument("--tenant-prefix", default="bench")
    parser.add_argument("--keep-data", action="store_true")
    return parser.parse_args()


def configure_dependencies(db_url: str) -> None:
    configure_vfs_dependencies(
        VFSDependencies(
            settings=BenchSettings(database_url=db_url),
            AgentWorkspace=BenchmarkWorkspaceModel,
            Chapter=BenchChapter,
            VirtualFileNode=BenchmarkFileNodeModel,
            VirtualShellCommand=BenchmarkShellCommandModel,
            VirtualShellSession=BenchmarkShellSessionModel,
            load_project_state_payload=lambda *args, **kwargs: {},
        )
    )

    from iruka_vfs import service as vfs_service

    vfs_service._redis_client = InMemoryRedis()


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize_latencies(name: str, samples: list[float], *, units: str = "ms", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    if not samples:
        payload = {
            "name": name,
            "units": units,
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "median": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "stdev": 0.0,
        }
        if extra:
            payload.update(extra)
        return payload
    payload = {
        "name": name,
        "units": units,
        "count": len(samples),
        "min": min(samples),
        "max": max(samples),
        "mean": statistics.fmean(samples),
        "median": statistics.median(samples),
        "p95": percentile(samples, 0.95),
        "p99": percentile(samples, 0.99),
        "stdev": statistics.stdev(samples) if len(samples) > 1 else 0.0,
    }
    if extra:
        payload.update(extra)
    return payload


def render_size_target(size_bytes: int, *, marker_count: int = 256) -> str:
    line = "Alpha beta gamma benchmark marker.\n"
    repeated = (size_bytes // len(line)) + 2
    text_value = (line * repeated)[:size_bytes].rstrip("\n")
    if "benchmark marker" not in text_value:
        text_value += "\nbenchmark marker"
    marker_block = "\n".join(f"MARKER_{i:03d}" for i in range(marker_count + 1))
    return text_value + "\n" + marker_block + "\n"


def command_latency_ms(session_factory: sessionmaker, workspace_handle: Any, command: str, iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        with session_factory() as db:
            started = time.perf_counter()
            result = workspace_handle.bash(db, command)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if result["exit_code"] != 0:
                raise RuntimeError(f"command failed: {command}\nstderr={result['stderr']}")
            samples.append(elapsed_ms)
    return samples


def flush_attempts(
    workspace_handle: Any,
    iterations: int,
    *,
    retries: int = 5,
    retry_sleep_seconds: float = 0.05,
) -> dict[str, Any]:
    samples: list[float] = []
    failures = 0
    for _ in range(iterations):
        elapsed_ms = 0.0
        ok = False
        for attempt in range(retries):
            started = time.perf_counter()
            ok = workspace_handle.flush()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if ok:
                samples.append(elapsed_ms)
                break
            if attempt + 1 < retries:
                time.sleep(retry_sleep_seconds)
        if not ok:
            failures += 1
    return {
        "samples_ms": samples,
        "failures": failures,
        "attempts": iterations,
    }


def prepare_workspace(
    session_factory: sessionmaker,
    *,
    tenant_id: str,
    runtime_key: str,
    chapter_id: int,
    chapter_text: str,
    context_files: dict[str, str],
    skill_files: dict[str, str],
) -> BenchmarkWorkspace:
    chapter_store = MutableText(chapter_text)
    with session_factory() as db:
        workspace_row = BenchmarkWorkspaceModel(
            tenant_id=tenant_id,
            conversation_id=1,
            project_id=1,
            chapter_id=1,
            status="idle",
            current_objective="benchmark",
        )
        db.add(workspace_row)
        db.commit()
        db.refresh(workspace_row)

        handle = create_workspace(
            workspace=workspace_row,
            tenant_id=tenant_id,
            runtime_key=runtime_key,
            chapter_id=chapter_id,
            primary_file=WritableFileSource(
                file_id=f"chapter:{chapter_id}",
                virtual_path=f"/workspace/chapters/chapter_{chapter_id}.md",
                read_text=chapter_store.read,
                write_text=chapter_store.write,
                metadata={"source_type": "benchmark"},
            ),
            context_files=context_files,
            skill_files=skill_files,
        )
        handle.ensure(db, include_tree=False)
        db.commit()

    return BenchmarkWorkspace(
        tenant_id=tenant_id,
        workspace_id=int(workspace_row.id),
        virtual_chapter_id=chapter_id,
        workspace=workspace_row,
        handle=handle,
        chapter_store=chapter_store,
    )


def cleanup_benchmark_data(session_factory: sessionmaker, tenant_pattern: str) -> None:
    for attempt in range(5):
        with session_factory() as db:
            try:
                db.execute(delete(BenchmarkShellCommandModel).where(BenchmarkShellCommandModel.tenant_id.like(tenant_pattern)))
                db.execute(delete(BenchmarkShellSessionModel).where(BenchmarkShellSessionModel.tenant_id.like(tenant_pattern)))
                db.execute(delete(BenchmarkPatchModel).where(BenchmarkPatchModel.tenant_id.like(tenant_pattern)))
                db.execute(delete(BenchmarkFileNodeModel).where(BenchmarkFileNodeModel.tenant_id.like(tenant_pattern)))
                db.execute(delete(BenchmarkWorkspaceModel).where(BenchmarkWorkspaceModel.tenant_id.like(tenant_pattern)))
                db.commit()
                return
            except IntegrityError:
                db.rollback()
        time.sleep(0.1 * (attempt + 1))
    with session_factory() as db:
        db.execute(delete(BenchmarkShellCommandModel).where(BenchmarkShellCommandModel.tenant_id.like(tenant_pattern)))
        db.execute(delete(BenchmarkShellSessionModel).where(BenchmarkShellSessionModel.tenant_id.like(tenant_pattern)))
        db.execute(delete(BenchmarkPatchModel).where(BenchmarkPatchModel.tenant_id.like(tenant_pattern)))
        db.execute(delete(BenchmarkFileNodeModel).where(BenchmarkFileNodeModel.tenant_id.like(tenant_pattern)))
        db.execute(delete(BenchmarkWorkspaceModel).where(BenchmarkWorkspaceModel.tenant_id.like(tenant_pattern)))
        db.commit()


def workspace_job(session_factory: sessionmaker, bench_workspace: BenchmarkWorkspace, commands_per_workspace: int) -> dict[str, Any]:
    command_latencies_ms: list[float] = []
    exit_codes: list[int] = []
    chapter_path = f"/workspace/chapters/chapter_{bench_workspace.virtual_chapter_id}.md"
    command_plan = [
        "cat /workspace/chapters/chapter_1.md",
        "wc /workspace/chapters/chapter_1.md",
        "rg marker /workspace",
        "echo worker-note > /workspace/notes/worker_note.txt",
        "cat /workspace/notes/worker_note.txt",
        "__edit_chapter__",
    ]
    for i in range(commands_per_workspace):
        command = command_plan[i % len(command_plan)]
        if command == "__edit_chapter__":
            if (i // len(command_plan)) % 2 == 0:
                command = f"edit {chapter_path} --find Alpha --replace ALPHA"
            else:
                command = f"edit {chapter_path} --find ALPHA --replace Alpha"
        elif "chapter_1.md" in command:
            command = command.replace("chapter_1.md", f"chapter_{bench_workspace.virtual_chapter_id}.md")
        with session_factory() as db:
            started = time.perf_counter()
            result = bench_workspace.handle.bash(db, command)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            command_latencies_ms.append(elapsed_ms)
            exit_codes.append(int(result["exit_code"]))
            if result["exit_code"] != 0:
                raise RuntimeError(
                    f"workspace {bench_workspace.workspace_id} command failed: {command}\n"
                    f"stderr={result['stderr']}"
                )
    flush_result = flush_attempts(bench_workspace.handle, 1)
    return {
        "workspace_id": bench_workspace.workspace_id,
        "command_count": len(command_latencies_ms),
        "command_latencies_ms": command_latencies_ms,
        "flush_ms": flush_result["samples_ms"][0] if flush_result["samples_ms"] else 0.0,
        "flush_failures": flush_result["failures"],
        "nonzero_exit_codes": sum(1 for code in exit_codes if code != 0),
    }


def measure_ensure_latency_ms(session_factory: sessionmaker, workspace_handle: Any, iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        with session_factory() as db:
            started = time.perf_counter()
            workspace_handle.ensure(db, include_tree=False)
            samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def finalize_latency_suite(
    session_factory: sessionmaker,
    bench_workspace: BenchmarkWorkspace,
    *,
    warmup_iterations: int,
    latency_iterations: int,
) -> list[dict[str, Any]]:
    chapter_path = f"/workspace/chapters/chapter_{bench_workspace.virtual_chapter_id}.md"
    results: list[dict[str, Any]] = []

    warmup_commands = [
        f"cat {chapter_path}",
        f"rg marker {chapter_path}",
        f"wc {chapter_path}",
        "echo warmup > /workspace/notes/warmup.txt",
    ]
    for command in warmup_commands:
        command_latency_ms(session_factory, bench_workspace.handle, command, warmup_iterations)

    results.append(summarize_latencies("ensure_warm", measure_ensure_latency_ms(session_factory, bench_workspace.handle, latency_iterations)))
    results.append(summarize_latencies("cat_chapter", command_latency_ms(session_factory, bench_workspace.handle, f"cat {chapter_path}", latency_iterations)))
    results.append(summarize_latencies("wc_chapter", command_latency_ms(session_factory, bench_workspace.handle, f"wc {chapter_path}", latency_iterations)))
    results.append(summarize_latencies("search_workspace", command_latency_ms(session_factory, bench_workspace.handle, "rg marker /workspace", latency_iterations)))

    note_samples: list[float] = []
    for i in range(latency_iterations):
        command = f"echo payload-{i} > /workspace/notes/note_{i}.txt"
        note_samples.extend(command_latency_ms(session_factory, bench_workspace.handle, command, 1))
    results.append(summarize_latencies("write_note_redirect", note_samples))

    edit_samples: list[float] = []
    for i in range(latency_iterations):
        marker = f"MARKER_{i:03d}"
        next_marker = f"MARKER_{i + 1:03d}"
        command = f"edit {chapter_path} --find {marker} --replace {next_marker}"
        edit_samples.extend(command_latency_ms(session_factory, bench_workspace.handle, command, 1))
    results.append(summarize_latencies("edit_chapter", edit_samples))
    flush_result = flush_attempts(bench_workspace.handle, latency_iterations)
    results.append(
        summarize_latencies(
            "flush",
            flush_result["samples_ms"],
            extra={
                "attempts": flush_result["attempts"],
                "failures": flush_result["failures"],
                "success_rate": (
                    len(flush_result["samples_ms"]) / flush_result["attempts"]
                    if flush_result["attempts"] > 0
                    else 0.0
                ),
            },
        )
    )
    return results


def build_report(
    *,
    started_at: datetime,
    ended_at: datetime,
    args: argparse.Namespace,
    db_info: dict[str, Any],
    single_workspace: list[dict[str, Any]],
    concurrency: dict[str, Any],
    data_cleanup: dict[str, Any],
) -> dict[str, Any]:
    duration_seconds = (ended_at - started_at).total_seconds()
    return {
        "report_version": 1,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": duration_seconds,
        "database": db_info,
        "parameters": {
            "workspace_count": args.workspace_count,
            "commands_per_workspace": args.commands_per_workspace,
            "latency_iterations": args.latency_iterations,
            "warmup_iterations": args.warmup_iterations,
            "chapter_bytes": args.chapter_bytes,
            "tenant_prefix": args.tenant_prefix,
            "keep_data": args.keep_data,
        },
        "single_workspace_latency": single_workspace,
        "concurrency": concurrency,
        "cleanup": data_cleanup,
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# iruka_vfs PostgreSQL Benchmark Report",
        "",
        f"- Started: `{report['started_at']}`",
        f"- Ended: `{report['ended_at']}`",
        f"- Duration: `{report['duration_seconds']:.2f}s`",
        f"- Database host: `{report['database']['host']}`",
        f"- Database name: `{report['database']['database']}`",
        f"- Workspace count: `{report['parameters']['workspace_count']}`",
        f"- Commands per workspace: `{report['parameters']['commands_per_workspace']}`",
        f"- Chapter bytes: `{report['parameters']['chapter_bytes']}`",
        "",
        "## Single Workspace Latency",
        "",
        "| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report["single_workspace_latency"]:
        lines.append(
            f"| `{item['name']}` | {item['count']} | {item['mean']:.2f} | {item['median']:.2f} | "
            f"{item['p95']:.2f} | {item['p99']:.2f} | {item['max']:.2f} |"
        )
        if item["name"] == "flush":
            lines.append(
                f"| `flush_success_rate` | {item.get('attempts', 0)} attempts | {item.get('success_rate', 0.0) * 100:.1f}% | "
                f"{item.get('failures', 0)} failures | - | - | - |"
            )

    concurrency = report["concurrency"]
    lines.extend(
        [
            "",
            "## Concurrent Throughput",
            "",
            f"- Total commands: `{concurrency['total_commands']}`",
            f"- Successful commands: `{concurrency['successful_commands']}`",
            f"- Wall time: `{concurrency['wall_time_seconds']:.2f}s`",
            f"- Throughput: `{concurrency['throughput_qps']:.2f} commands/s`",
            f"- Command latency mean/p95: `{concurrency['command_latency_ms']['mean']:.2f} / {concurrency['command_latency_ms']['p95']:.2f} ms`",
            f"- Flush mean/p95: `{concurrency['flush_latency_ms']['mean']:.2f} / {concurrency['flush_latency_ms']['p95']:.2f} ms`",
            f"- Flush success rate: `{concurrency['flush_latency_ms'].get('success_rate', 0.0) * 100:.1f}%`",
            f"- Flush failures: `{concurrency.get('flush_failures', 0)}`",
            f"- Command log rows observed: `{concurrency.get('command_log_rows', 0)}`",
            "",
            "## Cleanup",
            "",
            f"- Performed: `{report['cleanup']['performed']}`",
            f"- Tenant pattern: `{report['cleanup']['tenant_pattern']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def ensure_parent_dir(file_path: Path) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    args = parse_args()
    started_at = utc_now()

    configure_dependencies(args.db_url)
    engine = create_engine(args.db_url, future=True, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)

    tenant_stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    tenant_root = f"{args.tenant_prefix}_{tenant_stamp}"
    chapter_text = render_size_target(args.chapter_bytes, marker_count=max(args.latency_iterations + 8, 256))
    context_files = {
        "outline.md": "# Outline\n\nbenchmark marker\n" * 16,
        "facts.md": "benchmark marker facts\n" * 32,
    }
    skill_files = {
        "style.md": "# Style\n\nKeep benchmark marker intact.\n",
    }

    primary_workspace = prepare_workspace(
        session_factory,
        tenant_id=f"{tenant_root}_primary",
        runtime_key=f"{tenant_root}:primary",
        chapter_id=1,
        chapter_text=chapter_text,
        context_files=context_files,
        skill_files=skill_files,
    )

    for _ in range(args.warmup_iterations):
        with session_factory() as db:
            primary_workspace.handle.ensure(db, include_tree=False)

    with session_factory() as db:
        probe = primary_workspace.handle.bash(db, "cat /workspace/chapters/chapter_1.md")
        if probe["exit_code"] != 0:
            raise RuntimeError(f"benchmark probe failed: {probe['stderr']}")

    single_workspace = finalize_latency_suite(
        session_factory,
        primary_workspace,
        warmup_iterations=args.warmup_iterations,
        latency_iterations=args.latency_iterations,
    )

    concurrent_workspaces = [
        prepare_workspace(
            session_factory,
            tenant_id=f"{tenant_root}_worker_{i:02d}",
            runtime_key=f"{tenant_root}:worker:{i}",
            chapter_id=i + 1,
            chapter_text=chapter_text,
            context_files=context_files,
            skill_files=skill_files,
        )
        for i in range(args.workspace_count)
    ]

    wall_started = time.perf_counter()
    worker_outputs: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workspace_count) as executor:
        futures = [
            executor.submit(workspace_job, session_factory, item, args.commands_per_workspace)
            for item in concurrent_workspaces
        ]
        for future in as_completed(futures):
            worker_outputs.append(future.result())
    wall_time_seconds = time.perf_counter() - wall_started

    all_command_latencies = [sample for item in worker_outputs for sample in item["command_latencies_ms"]]
    all_flush_latencies = [item["flush_ms"] for item in worker_outputs]
    flush_failures = sum(item["flush_failures"] for item in worker_outputs)
    total_commands = sum(item["command_count"] for item in worker_outputs)
    successful_commands = total_commands - sum(item["nonzero_exit_codes"] for item in worker_outputs)

    with session_factory() as db:
        command_rows = db.execute(
            text(
                """
                SELECT COUNT(*) AS row_count
                FROM virtual_shell_commands
                WHERE tenant_id LIKE :tenant_pattern
                """
            ),
            {"tenant_pattern": f"{tenant_root}%"},
        ).scalar_one()

    concurrency = {
        "workspace_count": args.workspace_count,
        "total_commands": total_commands,
        "successful_commands": successful_commands,
        "wall_time_seconds": wall_time_seconds,
        "throughput_qps": (total_commands / wall_time_seconds) if wall_time_seconds > 0 else 0.0,
        "command_log_rows": int(command_rows),
        "flush_failures": flush_failures,
        "command_latency_ms": summarize_latencies("concurrent_commands", all_command_latencies),
        "flush_latency_ms": summarize_latencies(
            "concurrent_flush",
            [value for value in all_flush_latencies if value > 0],
            extra={
                "attempts": len(worker_outputs),
                "failures": flush_failures,
                "success_rate": (
                    (len(worker_outputs) - flush_failures) / len(worker_outputs)
                    if worker_outputs
                    else 0.0
                ),
            },
        ),
    }

    cleanup = {
        "performed": False,
        "tenant_pattern": f"{tenant_root}%",
    }
    if not args.keep_data:
        cleanup_benchmark_data(session_factory, f"{tenant_root}%")
        cleanup["performed"] = True

    ended_at = utc_now()
    report = build_report(
        started_at=started_at,
        ended_at=ended_at,
        args=args,
        db_info={
            "host": "pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com",
            "database": "file_sys",
            "driver": engine.url.drivername,
        },
        single_workspace=single_workspace,
        concurrency=concurrency,
        data_cleanup=cleanup,
    )

    output_stamp = ended_at.strftime("%Y%m%dT%H%M%SZ")
    default_dir = REPO_ROOT / "benchmark_reports"
    json_path = Path(args.json_out) if args.json_out else default_dir / f"iruka_vfs_pg_benchmark_{output_stamp}.json"
    md_path = Path(args.markdown_out) if args.markdown_out else default_dir / f"iruka_vfs_pg_benchmark_{output_stamp}.md"
    ensure_parent_dir(json_path)
    ensure_parent_dir(md_path)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")

    print(json.dumps({"json_report": str(json_path), "markdown_report": str(md_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
