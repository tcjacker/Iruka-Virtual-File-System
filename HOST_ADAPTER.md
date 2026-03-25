# Host Adapter Contract

[中文说明](./HOST_ADAPTER.zh-CN.md)

`iruka_vfs` owns the VFS runtime.

The host service owns:

- conversations and requests
- documents, records, or other source entities
- project or domain state
- runtime selection for one agent execution

The host adapter translates host concepts into VFS concepts.

## Current Package Shape

After the refactor, a host integration should think in these layers:

- public package entry: `iruka_vfs/__init__.py`, `iruka_vfs/workspace.py`
- workspace handle and factory: `iruka_vfs/sdk/`
- orchestration entry points: `iruka_vfs/service_ops/`
- execution internals: `iruka_vfs/runtime/`
- workspace-state internals: `iruka_vfs/mirror/`, `iruka_vfs/cache/`, `iruka_vfs/pathing/`, `iruka_vfs/sqlalchemy_repo/`

Compatibility modules such as `iruka_vfs/service.py` and `iruka_vfs/workspace_mirror.py`
still exist, but they should be treated as facades that preserve older imports.

## Responsibilities

The host adapter should:

1. Resolve host context such as `tenant_id`, `runtime_key`, and source record identifiers
2. Build one workspace object for one agent
3. Materialize host-side content into `workspace_files`
5. Call `workspace.ensure(db)` before command execution
6. Call `workspace.bash(db, "...")` for each virtual command
7. Call `workspace.flush()` at turn end or another explicit durability boundary

The host adapter should not push host-only business models into VFS APIs.

## Recommended API

```python
from iruka_vfs import build_workspace_seed, create_workspace

workspace = create_workspace(
    workspace=workspace_model,
    tenant_id=str(workspace_model.tenant_id),
    workspace_seed=build_workspace_seed(
        runtime_key=str(workspace_model.runtime_key),
        tenant_id=str(workspace_model.tenant_id),
        workspace_files={
            f"/workspace/files/document_{document.id}.md": document.body_text,
            "/workspace/docs/brief.md": initial_brief_text,
            "/workspace/docs/outline.md": outline_text,
            "/workspace/docs/style.md": style_text,
        },
    ),
)

workspace.ensure(db)
workspace.write_file(db, "/workspace/docs/generated.md", "from host adapter")
brief_text = workspace.read_file(db, "/workspace/docs/brief.md")
doc_files = workspace.read_directory(db, "/workspace/docs")
workspace.enter_agent_mode(db)
result = workspace.bash(db, "edit /workspace/files/document_123.md --find foo --replace bar")
workspace.enter_host_mode(db)
workspace.flush()
```

`RuntimeSeed` still exists internally, but the preferred host-facing API is the workspace object.

## Agent Call Flow

The normal host-driven execution path is:

```text
create_workspace(...)
  -> sdk.workspace_factory.create_workspace_handle(...)
  -> VirtualWorkspace

workspace.ensure(db)
  -> service.ensure_virtual_workspace(...)
  -> service_ops.bootstrap.ensure_virtual_workspace(...)

workspace.bash(db, "...")
  -> service.run_virtual_bash(...)
  -> service_ops.file_api.run_virtual_bash(...)
  -> runtime.executor.run_command_chain(...)

workspace.flush()
  -> service.flush_workspace(...)
  -> service_ops.file_api.flush_workspace(...)
  -> mirror.checkpoint.flush_workspace_mirror(...)
```

The host adapter should only depend on the public workspace methods unless there is a
strong reason to reach into lower-level modules.

## Lifecycle Rules

The host should model one workspace as one agent execution context. A workspace may survive across multiple agent turns, but command execution on that workspace should stay serialized.

Required constraints:

- do not run concurrent commands on the same workspace
- do not share one live SQLAlchemy `Session` across requests or threads
- prefer storing only workspace identifiers and source bindings in a reusable adapter object
- bind the current request's DB session when calling `workspace.ensure(db)` and `workspace.bash(db, "...")`
- switch to `agent` mode before calling `workspace.bash(db, "...")`
- switch back to `host` mode before direct host-side file reads or writes
- keep `workspace.flush()` as an explicit end-of-turn durability action

This keeps Redis-backed workspace state reusable while avoiding stale DB sessions and request-crossing runtime objects.

## Minimal Mapping

Typical document-based host mapping:

- host conversation/request -> choose runtime/workspace
- host document/resource -> one VFS file like `/workspace/files/document_123.md`
- host project state -> supporting files under `/workspace/docs/...`

## Required Handle Inputs

At minimum the adapter must provide:

- `workspace`
- `runtime_key`
- `tenant_id`
- `workspace_seed`

## Reference Pattern

Keep host-specific seed builders outside this repository. A host integration layer should be the only place that knows how to turn a document or project record into VFS file sources.
