from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ScenarioConfig:
    name: str
    description: str
    slim_logging: bool
    log_write_enabled: bool
    checkpoint_enabled: bool


SCENARIOS: dict[str, ScenarioConfig] = {
    "slim_logging": ScenarioConfig(
        name="slim_logging",
        description="Current implementation: slim command logging + checkpoint enabled",
        slim_logging=True,
        log_write_enabled=True,
        checkpoint_enabled=True,
    ),
    "full_logging": ScenarioConfig(
        name="full_logging",
        description="Pre-slim behavior: full artifacts in command logs + checkpoint enabled",
        slim_logging=False,
        log_write_enabled=True,
        checkpoint_enabled=True,
    ),
    "no_log_write": ScenarioConfig(
        name="no_log_write",
        description="Command log writes disabled, checkpoint still enabled",
        slim_logging=True,
        log_write_enabled=False,
        checkpoint_enabled=True,
    ),
    "checkpoint_off": ScenarioConfig(
        name="checkpoint_off",
        description="Checkpoint disabled, slim logging kept enabled",
        slim_logging=True,
        log_write_enabled=True,
        checkpoint_enabled=False,
    ),
}


def load_benchmark_module():
    spec = importlib.util.spec_from_file_location("run_pg_benchmark", REPO_ROOT / "scripts/run_pg_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_pg_benchmark"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def scenario_metric(run: dict[str, Any], section: str, item_name: str, metric: str) -> float:
    if section == "single_workspace_latency":
        items = run[section]
        item = next(entry for entry in items if entry["name"] == item_name)
        return float(item[metric])
    if section == "concurrency":
        item = run[section]
        if item_name:
            return float(item[item_name][metric])
        return float(item[metric])
    raise ValueError(section)


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate_runs(config: ScenarioConfig, runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "scenario": config.name,
        "description": config.description,
        "repeats": len(runs),
        "parameters": runs[0]["parameters"] if runs else {},
        "averages": {
            "edit_mean_ms": average(
                [scenario_metric(run, "single_workspace_latency", "edit_chapter", "mean") for run in runs]
            ),
            "edit_p95_ms": average(
                [scenario_metric(run, "single_workspace_latency", "edit_chapter", "p95") for run in runs]
            ),
            "cat_mean_ms": average(
                [scenario_metric(run, "single_workspace_latency", "cat_chapter", "mean") for run in runs]
            ),
            "flush_mean_ms": average(
                [scenario_metric(run, "single_workspace_latency", "flush", "mean") for run in runs]
            ),
            "concurrency_qps": average([scenario_metric(run, "concurrency", "", "throughput_qps") for run in runs]),
            "concurrency_mean_ms": average(
                [scenario_metric(run, "concurrency", "command_latency_ms", "mean") for run in runs]
            ),
            "concurrency_p95_ms": average(
                [scenario_metric(run, "concurrency", "command_latency_ms", "p95") for run in runs]
            ),
        },
        "runs": runs,
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Logging Impact Experiment",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Database host: `{report['database']['host']}`",
        f"- Database name: `{report['database']['database']}`",
        "",
        "| Scenario | Edit Mean (ms) | Edit P95 (ms) | Cat Mean (ms) | Flush Mean (ms) | Concurrency QPS | Concurrency P95 (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report["scenarios"]:
        avg = item["averages"]
        lines.append(
            f"| `{item['scenario']}` | {avg['edit_mean_ms']:.2f} | {avg['edit_p95_ms']:.2f} | "
            f"{avg['cat_mean_ms']:.2f} | {avg['flush_mean_ms']:.2f} | "
            f"{avg['concurrency_qps']:.2f} | {avg['concurrency_p95_ms']:.2f} |"
        )
    return "\n".join(lines) + "\n"


def run_single_scenario(config: ScenarioConfig, repeat_index: int) -> dict[str, Any]:
    module = load_benchmark_module()
    module.configure_dependencies(module.default_db_url())

    from iruka_vfs import service
    from iruka_vfs import workspace_mirror

    if not config.slim_logging:
        service._prepare_artifacts_for_log = lambda artifacts: dict(artifacts or {})
    if not config.log_write_enabled:
        service.ASYNC_COMMAND_LOGGING = False
        object.__setattr__(
            service._repositories,
            "command_log",
            SimpleNamespace(
                create_command_log=lambda db, payload: 0,
                bulk_insert_command_logs=lambda db, payloads: None,
            ),
        )
    if not config.checkpoint_enabled:
        service._ensure_workspace_checkpoint_worker_api = lambda engine: None
        workspace_mirror.enqueue_workspace_checkpoint = lambda base_key: None

    engine = create_engine(module.default_db_url(), future=True, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)

    started_at = datetime.now(UTC)
    tenant_root = f"logexp_{config.name}_{started_at.strftime('%Y%m%dT%H%M%SZ')}_r{repeat_index}"
    chapter_text = module.render_size_target(64 * 1024, marker_count=256)
    workspace_files = {
        "/workspace/docs/outline.md": "# Outline\n\nbenchmark marker\n" * 16,
        "/workspace/docs/facts.md": "benchmark marker facts\n" * 32,
        "/workspace/docs/style.md": "# Style\n\nKeep benchmark marker intact.\n",
    }

    primary_workspace = module.prepare_workspace(
        session_factory,
        tenant_id=f"{tenant_root}_primary",
        runtime_key=f"{tenant_root}:primary",
        file_index=1,
        chapter_text=chapter_text,
        workspace_files=workspace_files,
    )

    for _ in range(1):
        with session_factory() as db:
            primary_workspace.handle.ensure(db, include_tree=False)

    single_workspace = module.finalize_latency_suite(
        session_factory,
        primary_workspace,
        warmup_iterations=1,
        latency_iterations=5,
    )

    concurrent_workspaces = [
        module.prepare_workspace(
            session_factory,
            tenant_id=f"{tenant_root}_worker_{i:02d}",
            runtime_key=f"{tenant_root}:worker:{i}",
            file_index=i + 1,
            chapter_text=chapter_text,
            workspace_files=workspace_files,
        )
        for i in range(2)
    ]

    wall_started = __import__("time").perf_counter()
    worker_outputs = [module.workspace_job(session_factory, item, 6) for item in concurrent_workspaces]
    wall_time_seconds = __import__("time").perf_counter() - wall_started

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
        "workspace_count": 2,
        "total_commands": total_commands,
        "successful_commands": successful_commands,
        "wall_time_seconds": wall_time_seconds,
        "throughput_qps": (total_commands / wall_time_seconds) if wall_time_seconds > 0 else 0.0,
        "command_log_rows": int(command_rows),
        "flush_failures": flush_failures,
        "command_latency_ms": module.summarize_latencies("concurrent_commands", all_command_latencies),
        "flush_latency_ms": module.summarize_latencies(
            "concurrent_flush",
            [value for value in all_flush_latencies if value > 0],
            extra={
                "attempts": len(worker_outputs),
                "failures": flush_failures,
                "success_rate": ((len(worker_outputs) - flush_failures) / len(worker_outputs)) if worker_outputs else 0.0,
            },
        ),
    }

    module.cleanup_benchmark_data(session_factory, f"{tenant_root}%")
    ended_at = datetime.now(UTC)
    return {
        "scenario": config.name,
        "repeat_index": repeat_index,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "parameters": {
            "workspace_count": 2,
            "commands_per_workspace": 6,
            "latency_iterations": 5,
            "warmup_iterations": 1,
            "chapter_bytes": 64 * 1024,
        },
        "single_workspace_latency": single_workspace,
        "concurrency": concurrency,
    }


def orchestrate(output_json: Path, output_md: Path) -> None:
    scenario_order = ["full_logging", "slim_logging", "no_log_write", "checkpoint_off"]
    all_results: list[dict[str, Any]] = []
    for scenario_name in scenario_order:
        for repeat_index in range(1, 4):
            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--mode",
                    "single",
                    "--scenario",
                    scenario_name,
                    "--repeat-index",
                    str(repeat_index),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            all_results.append(json.loads(proc.stdout))

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "database": {
            "host": "pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com",
            "database": "file_sys",
        },
        "scenarios": [
            aggregate_runs(SCENARIOS[name], [item for item in all_results if item["scenario"] == name])
            for name in scenario_order
        ],
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_md.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"json_report": str(output_json), "markdown_report": str(output_md)}, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run logging/checkpoint impact experiments.")
    parser.add_argument("--mode", choices=["single", "orchestrate"], default="orchestrate")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS.keys()), default="slim_logging")
    parser.add_argument("--repeat-index", type=int, default=1)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--markdown-out", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "single":
        result = run_single_scenario(SCENARIOS[args.scenario], args.repeat_index)
        print(json.dumps(result, ensure_ascii=False))
        return

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    default_dir = REPO_ROOT / "benchmark_reports"
    json_out = Path(args.json_out) if args.json_out else default_dir / f"logging_impact_experiment_{stamp}.json"
    markdown_out = Path(args.markdown_out) if args.markdown_out else default_dir / f"logging_impact_experiment_{stamp}.md"
    orchestrate(json_out, markdown_out)


if __name__ == "__main__":
    main()
