from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def load_benchmark_module():
    spec = importlib.util.spec_from_file_location("run_pg_benchmark", "scripts/run_pg_benchmark.py")
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


def summarize(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"count": 0, "mean_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(samples)
    return {
        "count": len(samples),
        "mean_ms": sum(samples) / len(samples),
        "median_ms": ordered[len(ordered) // 2],
        "p95_ms": percentile(samples, 0.95),
        "p99_ms": percentile(samples, 0.99),
        "max_ms": max(samples),
    }


def run_edit_series(module, session_local, *, tenant: str, iterations: int, disable_checkpoint: bool) -> dict[str, object]:
    from iruka_vfs import service
    from iruka_vfs import workspace_mirror

    original_ensure_worker = service._ensure_workspace_checkpoint_worker_api
    original_enqueue = workspace_mirror.enqueue_workspace_checkpoint
    original_async_logging = service.ASYNC_COMMAND_LOGGING
    service.ASYNC_COMMAND_LOGGING = False
    if disable_checkpoint:
        service._ensure_workspace_checkpoint_worker_api = lambda engine: None
        workspace_mirror.enqueue_workspace_checkpoint = lambda base_key: None

    try:
        module.cleanup_benchmark_data(session_local, tenant + "%")
        workspace = module.prepare_workspace(
            session_local,
            tenant_id=tenant,
            runtime_key=tenant + ":edit-profile",
            file_index=1,
            chapter_text=module.render_size_target(64 * 1024, marker_count=max(iterations + 8, 256)),
            workspace_files={
                "/workspace/docs/outline.md": "benchmark marker\n" * 8,
                "/workspace/docs/style.md": "keep edits deterministic\n",
            },
        )

        latencies: list[float] = []
        paths: list[dict[str, object]] = []
        chapter_path = "/workspace/files/document_1.md"
        for i in range(iterations):
            command = f"edit {chapter_path} --find MARKER_{i:03d} --replace MARKER_{i + 1:03d}"
            with session_local() as db:
                started = time.perf_counter()
                result = workspace.handle.bash(db, command)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
            if result["exit_code"] != 0:
                raise RuntimeError(f"edit failed in mode disable_checkpoint={disable_checkpoint}: {result['stderr']}")
            latencies.append(elapsed_ms)
            paths.append({"iteration": i, "latency_ms": elapsed_ms})

        return {
            "disable_checkpoint": disable_checkpoint,
            "summary": summarize(latencies),
            "samples": paths,
        }
    finally:
        time.sleep(0.2)
        module.cleanup_benchmark_data(session_local, tenant + "%")
        service._ensure_workspace_checkpoint_worker_api = original_ensure_worker
        workspace_mirror.enqueue_workspace_checkpoint = original_enqueue
        service.ASYNC_COMMAND_LOGGING = original_async_logging


def main() -> None:
    module = load_benchmark_module()
    module.configure_dependencies(module.default_db_url())
    engine = create_engine(module.default_db_url(), future=True, pool_pre_ping=True)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    baseline = run_edit_series(
        module,
        session_local,
        tenant=f"edit_profile_baseline_{stamp}",
        iterations=24,
        disable_checkpoint=False,
    )
    no_checkpoint = run_edit_series(
        module,
        session_local,
        tenant=f"edit_profile_no_checkpoint_{stamp}",
        iterations=24,
        disable_checkpoint=True,
    )

    print(json.dumps({"baseline": baseline, "no_checkpoint": no_checkpoint}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
