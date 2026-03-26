from __future__ import annotations

import importlib.util
import json
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def load_benchmark_module():
    spec = importlib.util.spec_from_file_location("run_pg_benchmark", "scripts/run_pg_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_pg_benchmark"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> None:
    mod = load_benchmark_module()
    mod.configure_dependencies(mod.default_db_url())

    engine = create_engine(mod.default_db_url(), future=True, pool_pre_ping=True)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)

    tenant = "flush_diag_once"
    mod.cleanup_benchmark_data(session_local, tenant + "%")
    ws = mod.prepare_workspace(
        session_local,
        tenant_id=tenant,
        runtime_key=tenant + ":1",
        file_index=1,
        chapter_text=mod.render_size_target(4096),
        workspace_files={
            "/workspace/docs/outline.md": "x",
            "/workspace/docs/style.md": "y",
        },
    )

    import iruka_vfs.runtime_state as runtime_state
    from iruka_vfs import service
    from iruka_vfs.workspace_mirror import workspace_base_key, workspace_error_key, workspace_mirror_key

    print("checkpoint_session_before", runtime_state.workspace_checkpoint_session_maker is not None)
    print("checkpoint_worker_before", runtime_state.workspace_checkpoint_worker_started)

    with session_local() as db:
        result = ws.handle.bash(db, "echo x > /workspace/runtime/a.txt")
        print("bash_result", json.dumps(result, ensure_ascii=False))

    print("checkpoint_session_after_bash", runtime_state.workspace_checkpoint_session_maker is not None)
    print("checkpoint_worker_after_bash", runtime_state.workspace_checkpoint_worker_started)

    mirror = service._get_workspace_mirror_api(ws.workspace_id, tenant_key=tenant)
    assert mirror is not None
    print(
        "mirror_state",
        json.dumps(
            {
                "dirty_content_node_ids": sorted(int(item) for item in mirror.dirty_content_node_ids),
                "dirty_structure_node_ids": sorted(int(item) for item in mirror.dirty_structure_node_ids),
                "dirty_session": bool(mirror.dirty_session),
                "dirty_workspace_metadata": bool(mirror.dirty_workspace_metadata),
                "revision": int(mirror.revision),
                "checkpoint_revision": int(mirror.checkpoint_revision),
                "scope_key": mirror.scope_key,
            },
            ensure_ascii=False,
        ),
    )

    ok = ws.handle.flush()
    print("flush_ok", ok)

    client = service._get_redis_client()
    base_key = workspace_base_key(tenant, ws.workspace_id, mirror.scope_key)
    print("error_raw", client.get(workspace_error_key(base_key)))
    print("mirror_raw_exists", bool(client.get(workspace_mirror_key(base_key))))

    mod.cleanup_benchmark_data(session_local, tenant + "%")


if __name__ == "__main__":
    main()
