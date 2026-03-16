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


def main() -> None:
    module = load_benchmark_module()
    module.configure_dependencies(module.default_db_url())

    from iruka_vfs import service
    from iruka_vfs import workspace_mirror

    service.ASYNC_COMMAND_LOGGING = False
    service._ensure_workspace_checkpoint_worker_api = lambda engine: None
    workspace_mirror.enqueue_workspace_checkpoint = lambda base_key: None

    engine = create_engine(module.default_db_url(), future=True, pool_pre_ping=True)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results: list[dict[str, object]] = []
    for chapter_bytes in (4 * 1024, 64 * 1024, 256 * 1024):
        tenant = f"edit_size_{chapter_bytes}_{stamp}"
        module.cleanup_benchmark_data(session_local, tenant + "%")
        workspace = module.prepare_workspace(
            session_local,
            tenant_id=tenant,
            runtime_key=tenant + ":1",
            file_index=1,
            chapter_text=module.render_size_target(chapter_bytes, marker_count=128),
            context_files={"outline.md": "benchmark marker\n"},
            skill_files={"style.md": "keep edits deterministic\n"},
        )

        chapter_path = "/workspace/chapters/chapter_1.md"
        samples: list[float] = []
        for i in range(12):
            with session_local() as db:
                started = time.perf_counter()
                result = workspace.handle.bash(
                    db,
                    f"edit {chapter_path} --find MARKER_{i:03d} --replace MARKER_{i + 1:03d}",
                )
                elapsed_ms = (time.perf_counter() - started) * 1000.0
            if result["exit_code"] != 0:
                raise RuntimeError(result["stderr"])
            samples.append(elapsed_ms)

        results.append(
            {
                "chapter_bytes": chapter_bytes,
                "summary": summarize(samples),
                "samples": samples,
            }
        )
        module.cleanup_benchmark_data(session_local, tenant + "%")

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
