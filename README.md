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
- `workspace.tool_write(db, path, content)`
- `workspace.tool_edit(db, path, old_text, new_text, replace_all=False)`
- `workspace.bash(db, "...")`
- `workspace.flush()`
- `iruka_vfs.service.snapshot_virtual_fs_cache_metrics()`

## Integration Model

The recommended integration pattern is:

1. Configure dependencies once at process startup
2. Build one workspace handle for one agent
3. Bind one writable host file plus readonly context and skill files
4. Prefer `workspace.tool_write(...)` and `workspace.tool_edit(...)` for structured file mutations, and use `workspace.bash(...)` for controlled shell-style reads/exploration
5. Call `workspace.flush()` at a clear durability boundary

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
workspace.write_file(db, "/workspace/docs/generated.md", "hello from host")
workspace.tool_write(db, "/workspace/docs/page.html", "<section>Hello</section>\n")
workspace.tool_edit(db, "/workspace/docs/page.html", "Hello", "Hello Dog Cafe")
content = workspace.read_file(db, "/workspace/docs/brief.md")
files = workspace.read_directory(db, "/workspace/docs")
workspace.enter_agent_mode(db)
result = workspace.bash(db, "cat /workspace/chapters/chapter_123.md")
workspace.enter_host_mode(db)
workspace.flush()
```

This facade is intentionally lightweight. It can be reused across turns for the same agent/workspace identity, but it should not be used for concurrent command execution.

## Host File API

Besides `workspace.bash(...)`, the host can manage virtual workspace files directly through Python APIs.

- `create_workspace(..., workspace_files={path: content, ...})`
- `workspace.write_file(db, path, content)`
- `workspace.tool_write(db, path, content)`
- `workspace.tool_edit(db, path, old_text, new_text, replace_all=False)`
- `workspace.read_file(db, path)`
- `workspace.read_directory(db, path, recursive=True)`
- `workspace.enter_agent_mode(db)` / `workspace.enter_host_mode(db)`

Notes:

- Relative paths are resolved under `/workspace`
- Parent directories are created automatically on write
- Paths must stay under `/workspace`
- `read_directory(...)` returns a `{virtual_path: content}` mapping
- `tool_write(...)` is the recommended structured equivalent of a full-file `write`
- `tool_edit(...)` is the recommended structured equivalent of a targeted text `edit`; it requires exactly one match unless `replace_all=True`
- `write_file(...)`, `read_file(...)`, and `read_directory(...)` require `host` mode
- `tool_write(...)` and `tool_edit(...)` require `host` mode
- `workspace.bash(...)` requires `agent` mode

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
