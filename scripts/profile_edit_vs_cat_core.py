from __future__ import annotations

import importlib.util
import json
import sys
import time
from types import SimpleNamespace
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
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "count": len(samples),
        "mean_ms": sum(samples) / len(samples),
        "median_ms": ordered[len(ordered) // 2],
        "p95_ms": percentile(samples, 0.95),
        "max_ms": max(samples),
    }


def run_case(*, enable_checkpoint: bool) -> dict[str, object]:
    module = load_benchmark_module()
    module.configure_dependencies(module.default_db_url())

    from iruka_vfs import service
    from iruka_vfs import workspace_mirror

    service.ASYNC_COMMAND_LOGGING = False
    if not enable_checkpoint:
        service._ensure_workspace_checkpoint_worker_api = lambda engine: None
        workspace_mirror.enqueue_workspace_checkpoint = lambda base_key: None
    object.__setattr__(service._repositories, "command_log", SimpleNamespace(create_command_log=lambda db, payload: 0))

    engine = create_engine(module.default_db_url(), future=True, pool_pre_ping=True)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tenant = f"edit_vs_cat_core_{'cp_on' if enable_checkpoint else 'cp_off'}_{stamp}"
    module.cleanup_benchmark_data(session_local, tenant + "%")
    workspace = module.prepare_workspace(
        session_local,
        tenant_id=tenant,
        runtime_key=tenant + ":1",
        file_index=1,
        chapter_text=module.render_size_target(64 * 1024, marker_count=128),
        workspace_files={
            "/workspace/docs/outline.md": "benchmark marker\n",
            "/workspace/docs/style.md": "keep edits deterministic\n",
        },
    )

    cat_samples: list[float] = []
    edit_samples: list[float] = []
    chapter_path = "/workspace/files/document_1.md"
    for i in range(12):
        with session_local() as db:
            started = time.perf_counter()
            cat_result = workspace.handle.bash(db, f"cat {chapter_path}")
            cat_elapsed_ms = (time.perf_counter() - started) * 1000.0
        if cat_result["exit_code"] != 0:
            raise RuntimeError(cat_result["stderr"])
        cat_samples.append(cat_elapsed_ms)

        with session_local() as db:
            started = time.perf_counter()
            edit_result = workspace.handle.bash(
                db,
                f"edit {chapter_path} --find MARKER_{i:03d} --replace MARKER_{i + 1:03d}",
            )
            edit_elapsed_ms = (time.perf_counter() - started) * 1000.0
        if edit_result["exit_code"] != 0:
            raise RuntimeError(edit_result["stderr"])
        edit_samples.append(edit_elapsed_ms)

    output = {
        "checkpoint_enabled": enable_checkpoint,
        "cat": summarize(cat_samples),
        "edit": summarize(edit_samples),
        "delta_mean_ms": (sum(edit_samples) / len(edit_samples)) - (sum(cat_samples) / len(cat_samples)),
    }
    module.cleanup_benchmark_data(session_local, tenant + "%")
    return output


def main() -> None:
    print(json.dumps({"checkpoint_on": run_case(enable_checkpoint=True), "checkpoint_off": run_case(enable_checkpoint=False)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
