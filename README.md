# `iruka_vfs`

`iruka_vfs` is a standalone VFS runtime for agent-driven editing workflows.

It owns:

- workspace runtime state
- virtual files and directories
- shell sessions and command logs
- cache and checkpoint flow

It does not own host business concepts such as `Conversation`.

## Repository Layout

```text
iruka_vfs_repo/
  iruka_vfs/
  examples/
  README.md
  HOST_ADAPTER.md
  pyproject.toml
```

## Public API

Stable entry points:

- `iruka_vfs.configure_vfs_dependencies(...)`
- `iruka_vfs.create_workspace(...)`
- `workspace.ensure(db)`
- `workspace.bash(db, "...")`
- `workspace.flush()`
- `iruka_vfs.service.snapshot_virtual_fs_cache_metrics()`

## Integration Model

The recommended integration pattern is:

1. Configure dependencies once at process startup
2. Build one workspace handle for one agent
3. Bind one writable host file plus readonly context and skill files
4. Call `workspace.bash(db, "...")` for command execution
5. Call `workspace.flush()` at a clear durability boundary

See [`HOST_ADAPTER.md`](HOST_ADAPTER.md) for the host-side contract.

## Ideal SDK Shape

```python
from iruka_vfs import WritableFileSource, create_workspace

workspace = create_workspace(
    workspace=workspace_model,
    tenant_id="tenant-a",
    runtime_key="conv:1001",
    chapter_id=123,
    primary_file=WritableFileSource(
        file_id="chapter:123",
        virtual_path="/workspace/chapters/chapter_123.md",
        read_text=load_chapter_text,
        write_text=save_chapter_text,
    ),
    context_files={"outline.md": outline_text},
    skill_files={"style.md": style_text},
)

workspace.ensure(db)
result = workspace.bash(db, "cat /workspace/chapters/chapter_123.md")
workspace.flush()
```

This facade is intentionally lightweight. It can be reused across turns for the same agent/workspace identity, but it should not be used for concurrent command execution.

## Workspace Lifecycle

Treat one virtual workspace as the execution context for one agent. Reuse the same underlying workspace id across turns if needed, but do not issue concurrent `workspace.bash(db, "...")` calls against the same workspace from multiple requests or workers.

Recommended rules:

- one agent -> one workspace
- no concurrent command execution on the same workspace
- keep database sessions request-scoped rather than storing a long-lived `Session` inside a reusable workspace object
- call `workspace.flush()` explicitly at turn end or another clear durability boundary

In practice, the safest facade is a lightweight workspace object that stores identifiers and file-source config, while each command call receives the current request's DB session.

## Local Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Standalone Demo

Run the minimal demo from the repository root:

```bash
python examples/standalone_sqlite_demo.py
```

The demo uses:

- local SQLite
- demo SQLAlchemy models
- an in-memory fake Redis

It creates one workspace, mounts one writable chapter-like file into the VFS, runs `cat` and `edit`, and then flushes the workspace.

## Technical History

The original implementation notes and benchmark records are kept under [`docs/`](docs/README.md).
