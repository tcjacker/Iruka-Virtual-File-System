# `iruka_vfs`

[中文说明](./README.zh-CN.md)

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
  tests/
  docs/
  README.md
  HOST_ADAPTER.md
  pyproject.toml
```

See [`docs/architecture.md`](docs/architecture.md) for the current package layering and dependency direction.

## Public API

Stable entry points:

- `iruka_vfs.configure_vfs_dependencies(...)`
- `iruka_vfs.create_workspace(...)`
- `workspace.ensure(db)`
- `workspace.write(db, path, content)`
- `workspace.edit(db, path, old_text, new_text)`
- `workspace.read_file(db, path)`
- `workspace.read_directory(db, path)`
- `workspace.file_tree(db, path="/workspace")`
- `workspace.run(db, "...")`
- `workspace.flush()`

## Integration Model

The recommended integration pattern is:

1. Configure dependencies once at process startup
2. Build one workspace handle for one agent
3. Bind one writable host file plus readonly context and skill files
4. Use `workspace.write(...)`, `workspace.edit(...)`, `workspace.read_file(...)`, `workspace.read_directory(...)`, `workspace.file_tree(...)`, and `workspace.run(...)` as the converged public path
5. Call `workspace.ensure(db)` as an optional preflight when you need the workspace materialized
6. Call `workspace.flush()` at a clear durability boundary

See [`HOST_ADAPTER.md`](HOST_ADAPTER.md) for the host-side contract.

## Ideal SDK Shape

```python
from iruka_vfs import WritableFileSource, create_workspace

workspace = create_workspace(
    workspace=workspace_model,
    tenant_id="tenant-a",
    runtime_key="conv:1001",
    primary_file=WritableFileSource(
        file_id="chapter:123",
        virtual_path="/workspace/chapters/chapter_123.md",
        read_text=load_chapter_text,
        write_text=save_chapter_text,
    ),
    workspace_files={
        "/workspace/docs/brief.md": "# Brief\n\nSeeded from Python.\n",
        "notes/todo.txt": "- inspect outline\n",
    },
    context_files={"outline.md": outline_text},
    skill_files={"style.md": style_text},
)

workspace.ensure(db)
workspace.write(db, "/workspace/docs/generated.md", "hello from host")
tree = workspace.file_tree(db, "/workspace/docs")
workspace.edit(db, "/workspace/docs/generated.md", "hello", "hello from host adapter")
content = workspace.read_file(db, "/workspace/docs/brief.md")
files = workspace.read_directory(db, "/workspace/docs")
result = workspace.run(db, "cat /workspace/chapters/chapter_123.md")
workspace.flush()
```

This facade is intentionally lightweight. It can be reused across turns for the same agent/workspace identity, but it should not be used for concurrent command execution.

## Host File API

The host can manage virtual workspace files directly through Python APIs.

- `create_workspace(..., workspace_files={path: content, ...})`
- `workspace.ensure(db)`
- `workspace.write(db, path, content)`
- `workspace.edit(db, path, old_text, new_text)`
- `workspace.read_file(db, path)`
- `workspace.read_directory(db, path, recursive=True)`
- `workspace.file_tree(db, path="/workspace")`
- `workspace.run(db, "...")`
- `workspace.flush()`

## Migration From Deprecated APIs

- `workspace.bash(db, cmd)` -> `workspace.run(db, cmd)`
- `workspace.write_file(db, path, content)` -> `workspace.write(db, path, content)`
- `workspace.tool_write(db, path, content)` -> `workspace.write(db, path, content)`
- `workspace.tool_edit(db, path, old_text, new_text)` -> `workspace.edit(db, path, old_text, new_text)`
- Explicit `enter_agent_mode(...)` / `enter_host_mode(...)` calls are no longer needed for the recommended host path.

Notes:

- Relative paths are resolved under `/workspace`
- Parent directories are created automatically on write
- Paths must stay under `/workspace`
- `file_tree(...)` returns the latest recursive tree from the active VFS mirror
- `read_directory(...)` returns a `{virtual_path: content}` mapping
- `write(...)` is the recommended structured equivalent of a full-file `write`
- `edit(...)` is the recommended structured equivalent of a targeted text `edit`
- Access mode switching is handled internally by the high-level API
- `workspace.ensure(...)` is an optional preflight for materializing the workspace state

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

It creates one workspace, mounts one writable chapter-like file into the VFS, runs `cat` and a text edit through the converged API, and then flushes the workspace.
