# `iruka_vfs`

[中文说明](./README.zh-CN.md)

`iruka_vfs` is a standalone VFS runtime for agent-driven editing workflows.

It owns:

- workspace runtime state
- virtual files and directories
- shell sessions and command logs
- cache and checkpoint flow

It does not own host business concepts such as `Conversation`.

## Quick Start

Start with these two documents:

- architecture: [docs/architecture.md](docs/architecture.md)
- API integration and runtime profiles: [docs/api_integration.en.md](docs/api_integration.en.md)

If you only need integration guidance, read `docs/api_integration.en.md` first.

Development setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .[dev]
python3 -m pytest
```

Quick sanity check:

```bash
python3 -m compileall iruka_vfs examples/standalone_sqlite_demo.py
```

## Runtime Profiles

| Profile | WorkspaceStateStore | VFSRepositories | External Dependencies | Recommended Use |
| --- | --- | --- | --- | --- |
| `persistent` | Redis | pgsql | Redis + PostgreSQL | production, durable state, recovery |
| `ephemeral-local` | local memory | memory | none | local dev, demos, lowest-friction setup |
| `ephemeral-redis` | Redis | memory | Redis | shared runtime state without database persistence |

Choose:

- `persistent` for durable production usage
- `ephemeral-local` for the lightest demo flow
- `ephemeral-redis` when you need shared runtime state but no PostgreSQL persistence

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

The current refactor splits the package into:

- public entry points: `iruka_vfs/__init__.py`, `iruka_vfs/workspace.py`
- workspace facade and factory: `iruka_vfs/sdk/`
- orchestration layer: `iruka_vfs/service_ops/`
- execution internals: `iruka_vfs/runtime/`
- mirror, pathing, cache, and repository internals: `iruka_vfs/mirror/`, `iruka_vfs/pathing/`, `iruka_vfs/cache/`, `iruka_vfs/sqlalchemy_repo/`
- compatibility facades kept for older imports: `service.py`, `command_runtime.py`, `memory_cache.py`, `paths.py`, `sqlalchemy_repositories.py`, `workspace_mirror.py`

## Public API

Stable entry points:

- `iruka_vfs.build_profile_dependencies(...)`
- `iruka_vfs.build_profile_persistent_dependencies(...)`
- `iruka_vfs.build_workspace_seed(...)`
- `iruka_vfs.configure_vfs_dependencies(...)`
- `iruka_vfs.create_workspace(...)`
- `workspace.ensure(db)`
- `workspace.bash(db, "...")`
- `workspace.flush()`
- `iruka_vfs.service.snapshot_virtual_fs_cache_metrics()`

Minimal setup:

```python
from iruka_vfs import build_profile_dependencies, configure_vfs_dependencies

configure_vfs_dependencies(
    build_profile_dependencies(
        settings=settings,
        runtime_profile="ephemeral-local",
    )
)
```

## Integration Model

The recommended integration pattern is:

1. Configure dependencies once at process startup
2. Build one workspace handle for one agent
3. Seed the workspace with `workspace_files`
4. Call `workspace.bash(db, "...")` for command execution
5. Call `workspace.flush()` at a clear durability boundary

See:

- [`HOST_ADAPTER.md`](HOST_ADAPTER.md) for the host adapter contract
- [`docs/api_integration.en.md`](docs/api_integration.en.md) for API usage, Redis, memory, and pgsql integration details

## Agent Integration

The recommended way to integrate with an agent runtime is:

1. Configure dependencies once at process startup
2. Build one `VirtualWorkspace` handle for one agent execution context
3. Call `workspace.ensure(db)` before running commands
4. Switch to agent mode with `workspace.enter_agent_mode(db)`
5. Run commands with `workspace.bash(db, "...")`
6. Switch back to host mode before direct host-side reads or writes
7. Call `workspace.flush()` at an explicit durability boundary

For host-side durability, `workspace.ensure(db)` also initializes the checkpoint persistence precondition used by `workspace.flush()`. After a normal `ensure(db)`, the host path does not need to initialize checkpoint worker state manually.

The virtual shell also provides a built-in `help` command. If the agent needs to re-check the supported surface at runtime, call `workspace.bash(db, "help")` and read `stdout` or `artifacts["supported_commands"]`. Each bash result also includes `workspace_outline`, `workspace_bootstrap`, `unique_filename_index`, `path_shortcuts`, and `discovery_hint` for path discovery, plus `task_guidance`, `verification_hint`, and `modified_paths` for long-horizon verification before the final answer. Parse failures also expose a structured `artifacts["parse_error"]` object, including explicit rewrite templates for unsupported multi-heredoc write chains.

Recommended minimal agent prompt:

```text
You are in a virtual workspace, not a full OS shell.

Use workspace.bash(db, "...") with only these commands:
pwd, cd, ls, cat, find, rg, grep, status, verify, wc -l, mkdir, touch, cp, mv, rm, sort, basename, dirname, edit, patch, tree, xargs, echo, help
Use `ls -l` when you need type/size/version/mtime.
When you know a filename but not its exact path, start with `find /workspace -name <name>`.
When the path is unknown, prefer: `find /workspace -name <name>` -> `cat` -> `edit` / `patch`.
When you need per-file match counts, prefer `grep -c <pattern> <path>` or `rg -c <pattern> <path>`.
When you need line-numbered matches, prefer `grep -n <pattern> <path>`.
For file-management steps, `cp`/`mv` are file-only, `rm` removes one file at a time, and `sort` is available for simple line sorting.

Write rules:
- stay under /workspace
- > does not overwrite existing files
- >| overwrites explicitly
- >> appends
- for multi-line file creation, you may use: cat <<'EOF' > /workspace/file ... EOF
- `edit` / `patch` also accept a single heredoc input
- one raw command must not chain multiple heredoc write blocks; split them with `;` or `&&`
- limited shell compatibility is available for `2>/dev/null` and restricted fallbacks `|| true`, `|| :`, `|| help`
- do not generate real-shell extras such as: general `||`, <, <<<, 1>, general 2>, &>, $(...), `...`

Before finishing a multi-file task:
- optionally run `status`
- inspect `task_guidance["verification"]["pending_verification_paths"]`
- or run `verify`
- run the suggested `cat ...` readback
- reuse `modified_paths` or `task_guidance["verification"]["changed_paths"]` in the final answer

If you are unsure what is supported, run: help
```

The core call path is:

```text
create_workspace(...)
  -> sdk.workspace_factory.create_workspace_handle(...)
  -> VirtualWorkspace

VirtualWorkspace.bash(...)
  -> service.run_virtual_bash(...)
  -> integrations.agent.shell.run_virtual_bash(...)
  -> mirror.mutation.execute_workspace_mirror_transaction(...)
  -> runtime.executor.run_command_chain(...)

VirtualWorkspace.flush()
  -> service.flush_workspace(...)
  -> service_ops.file_api.flush_workspace(...)
  -> mirror.checkpoint.resolve_workspace_ref_for_flush(...)
  -> mirror.checkpoint.run_checkpoint_cycle(...)
  -> mirror.checkpoint.flush_workspace_mirror(...)
```

## Ideal SDK Shape

```python
from iruka_vfs import build_workspace_seed, create_workspace

workspace = create_workspace(
    workspace=workspace_model,
    tenant_id="tenant-a",
    workspace_seed=build_workspace_seed(
        runtime_key="conv:1001",
        tenant_id="tenant-a",
        workspace_files={
            "/workspace/files/document_123.md": load_document_text(),
            "/workspace/docs/brief.md": "# Brief\n\nSeeded from Python.\n",
            "todo.txt": "- inspect outline\n",
        },
    ),
)

workspace.ensure(db)
conflict = workspace.write_file(db, "/workspace/docs/generated.md", "hello from host")
if conflict.get("conflict"):
    workspace.write_file(db, "/workspace/docs/generated.md", "hello from host", overwrite=True)
content = workspace.read_file(db, "/workspace/docs/brief.md")
files = workspace.read_directory(db, "/workspace/docs")
workspace.enter_agent_mode(db)
result = workspace.bash(db, "cat /workspace/files/document_123.md")
workspace.enter_host_mode(db)
workspace.flush()
```

This facade is intentionally lightweight. It can be reused across turns for the same agent/workspace identity, but it should not be used for concurrent command execution.

`create_workspace(...)` takes a generic `workspace_seed`. Build it with `build_workspace_seed(...)` and put all initial files into `workspace_files`.

In Redis-backed profiles, Redis is the runtime source of truth. In-process mirror objects are only short-lived working objects inside one transaction or command chain.
On the host path, a successful `ensure(db)` also prepares the checkpoint persistence path used by later `workspace.flush()` calls.
The handle also binds to the first real persistence target it sees. After the first successful `ensure/read/write/bash` with a real DB session, the same workspace handle must not be reused against a different database target.

## Host File API

Besides `workspace.bash(...)`, the host can manage virtual workspace files directly through Python APIs.

- `create_workspace(..., workspace_files={path: content, ...})`
- `workspace.write_file(db, path, content, overwrite=False)`
- `workspace.read_file(db, path)`
- `workspace.read_directory(db, path, recursive=True)`
- `workspace.enter_agent_mode(db)` / `workspace.enter_host_mode(db)`

Current access-mode rules:

- Relative paths are resolved under `/workspace`
- Parent directories are created automatically on write
- Paths must stay under `/workspace`
- `read_directory(...)` returns a `{virtual_path: content}` mapping
- `write_file(...)` requires `host` mode
- `write_file(...)` does not overwrite an existing file unless `overwrite=True`
- `read_file(...)` and `read_directory(...)` are allowed in both `host` and `agent` mode
- `workspace.bash(...)` requires `agent` mode

Overwrite confirmation rules:

- `workspace.write_file(...)` returns a structured conflict payload when the target file already exists and `overwrite=False`
- shell redirect `>` also fails with a structured conflict payload when the target file already exists
- shell redirect `>|` is the explicit overwrite form

## Workspace Lifecycle

Treat one virtual workspace as the execution context for one agent. Reuse the same underlying workspace id across turns if needed, but do not issue concurrent `workspace.bash(db, "...")` calls against the same workspace from multiple requests or workers.

Recommended rules:

- one agent -> one workspace
- no concurrent command execution on the same workspace
- keep database sessions request-scoped rather than storing a long-lived `Session` inside a reusable workspace object
- treat the persistence target as stable for one workspace handle; after first use, do not switch that handle to a different database
- call `workspace.flush()` explicitly at turn end or another clear durability boundary

In practice, the safest facade is a lightweight workspace object that stores identifiers and seed config, while each command call receives the current request's DB session.

## Local Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Demos

Run the minimal demo from the repository root:

```bash
python examples/standalone_sqlite_demo.py
```

The demo uses:

- local SQLite
- demo SQLAlchemy models
- an in-memory fake Redis

It creates one workspace, seeds files into the VFS, runs `cat` and `edit`, and then flushes the workspace.

Web demo:

```bash
python examples/vfs_web_demo.py --host 127.0.0.1 --port 8765
```

The web demo can switch between:

- `persistent`
- `ephemeral-local`
- `ephemeral-redis`
