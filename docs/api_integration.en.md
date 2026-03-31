## Iruka VFS API Integration

This document describes how to integrate `iruka_vfs` into a host system and how to choose between the three runtime profiles:

- `persistent`
- `ephemeral-local`
- `ephemeral-redis`

## 1. Core Model

The runtime is split into two layers:

- `WorkspaceStateStore`
  The live runtime state used directly by the agent. It owns the file tree, file content, cwd, dirty flags, locks, and checkpoint scheduling.
- `VFSRepositories`
  The persistence layer. It owns durable reads and writes for workspaces, sessions, nodes, and command logs.

Backend semantics:

- In `ephemeral-local`, the in-process mirror object is the runtime state itself.
- In Redis-backed profiles, Redis is the runtime source of truth and the in-process mirror object is only a short-lived transaction-local working object.

Profile mapping:

| Profile | WorkspaceStateStore | VFSRepositories | Typical Use |
| --- | --- | --- | --- |
| `persistent` | Redis | pgsql | production, durable recovery |
| `ephemeral-local` | local memory | memory | local dev, demos |
| `ephemeral-redis` | Redis | memory | shared runtime state without database persistence |

Dependency requirements:

| Profile | Needs Redis | Needs PostgreSQL | Durable |
| --- | --- | --- | --- |
| `persistent` | yes | yes | yes |
| `ephemeral-local` | no | no | no |
| `ephemeral-redis` | yes | no | no |

## 2. Public Entry Points

Recommended API surface:

```python
from iruka_vfs import (
    build_profile_dependencies,
    build_profile_persistent_dependencies,
    build_workspace_seed,
    configure_vfs_dependencies,
    create_workspace,
)
```

Responsibilities:

- `build_profile_dependencies(...)`
  Build dependencies from a selected runtime profile.
- `build_profile_persistent_dependencies(...)`
  Explicit builder for the `persistent` profile.
- `configure_vfs_dependencies(...)`
  Register the dependencies used by the current process.
- `create_workspace(...)`
  Build a `VirtualWorkspace` handle.
- `build_workspace_seed(...)`
  Build the seed used to initialize a virtual workspace.

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

Advanced integrations can still inject custom ORM models, repositories, or workspace-state stores through `VFSDependencies(...)`.

## 3. Integration Prerequisites

The host application typically provides:

1. A workspace model, for example `AgentWorkspace`
2. VFS ORM models
3. An optional `load_project_state_payload(...)` callback

Minimal callback shape:

```python
def load_project_state_payload(*args, **kwargs) -> dict:
    return {}
```

## 4. Integration Flow

### 4.1 Configure a Profile

```python
from iruka_vfs import build_profile_dependencies, configure_vfs_dependencies


dependencies = build_profile_dependencies(
    settings=settings,
    runtime_profile="ephemeral-local",  # or "persistent" / "ephemeral-redis"
)
configure_vfs_dependencies(dependencies)
```

Explicit `persistent` setup:

```python
from iruka_vfs import build_profile_persistent_dependencies, configure_vfs_dependencies


dependencies = build_profile_persistent_dependencies(settings=settings)
configure_vfs_dependencies(dependencies)
```

### 4.2 Create a Workspace Handle

Build a `WorkspaceSeed` explicitly and pass it to `create_workspace(...)`.

```python
from iruka_vfs import build_workspace_seed, create_workspace


workspace_handle = create_workspace(
    workspace=workspace_row,
    tenant_id=workspace_row.tenant_id,
    workspace_seed=build_workspace_seed(
        runtime_key=workspace_row.runtime_key,
        tenant_id=workspace_row.tenant_id,
        workspace_files={
            "/workspace/files/demo.md": "hello\n",
            "/workspace/docs/brief.md": "# Workspace\n\nDemo workspace.\n",
        },
    ),
)
```

### 4.3 Initialize the Workspace

```python
with SessionLocal() as db:
    snapshot = workspace_handle.ensure(db)
    print(snapshot.get("tree") or "")
```

Host-side persistence note:

- `ensure(db)` initializes the checkpoint persistence precondition used by later `workspace.flush()`
- after a normal `ensure(db)`, the host path does not need to manually prepare checkpoint worker state
- after the first successful operation with a real DB session, the same workspace handle is bound to that persistence target and must not be reused with another database target

### 4.4 Switch to Agent Mode and Run Commands

```python
with SessionLocal() as db:
    workspace_handle.enter_agent_mode(db)
    result = workspace_handle.bash(
        db,
        "cd /workspace/files && pwd && cat demo.md",
    )
    print(result["stdout"])
```

The virtual shell is intentionally small. Current supported commands are:

- `pwd`
- `cd`
- `ls`
  `ls -l` / `ls -la` show `type`, `size`, `version`, and `mtime`
- `cat`
- `find`
  Use `find /workspace -name brief.md` when you know the filename but not the path
- `rg`
  `rg -c TODO /workspace/docs` returns per-file match counts
- `grep`
  `grep -l TODO /workspace` returns matching file paths
  `grep -c TODO /workspace/docs` returns per-file match counts
  `grep -v .git` is useful for filtering stdin path lists
- `wc -l`
- `mkdir`
- `touch`
- `cp`
  File-only copy; it does not overwrite an existing target
- `mv`
  File-only move/rename; it does not overwrite an existing target
- `rm`
  Single-file remove only; no `-r` / `-f`
- `sort`
  Supports file input and stdin pipeline input, without advanced flags
- `basename`
- `dirname`
- `edit`
- `patch`
- `tree`
- `xargs`
  Supported in a limited form such as `find ... | xargs grep -l TODO`
- `echo`
- `help`

Use `workspace_handle.bash(db, "help")` when the agent needs the current command list and write rules at runtime.

Recommended agent bootstrap prompt:

```text
You are in a virtual workspace, not a full OS shell.

Use workspace.bash(db, "...") with only these commands:
pwd, cd, ls, cat, find, rg, grep, wc -l, mkdir, touch, edit, patch, tree, xargs, echo, help
Also available: cp, mv, rm, sort, basename, dirname
Use `ls -l` when you need type/size/version/mtime.
When you know the filename but not the path, start with `find /workspace -name <name>`.
When the path is unknown, prefer: `find /workspace -name <name>` -> `cat` -> `edit` / `patch`.
When you need file-path-only content matches, prefer `grep -l <pattern> /workspace`.
When you need per-file match counts, prefer `grep -c <pattern> <path>` or `rg -c <pattern> <path>`.
When you want a safe ignore-on-failure fallback, only use `|| true`, `|| :`, or `|| help`.

Write rules:
- stay under /workspace
- > does not overwrite existing files
- >| overwrites explicitly
- once you have confirmed an existing target file, prefer `>|` directly for rewrites
- >> appends
- for multi-line file creation, you may use: cat <<'EOF' > /workspace/file ... EOF
- `2>/dev/null` is supported in a limited form
- do not generate real-shell extras such as: general `||`, <, <<<, 1>, general 2>, &>, $(...), `...`

If you are unsure what is supported, run: help
```

Each `workspace_handle.bash(...)` result now also includes:

- `workspace_outline`
  A shallow directory skeleton for the current workspace.
- `workspace_bootstrap`
  A bounded bootstrap preview with suggested targets and unique filename hints.
- `unique_filename_index`
  A bounded `basename -> exact path` map for unique filenames.
- `path_shortcuts`
  Copyable exact-path helpers such as `brief.md: cat /workspace/docs/brief.md`.
- `discovery_hint`
  Recommended path-recovery flow.
- `task_guidance`
  Structured guidance for long-horizon tasks, including changed paths, pending verification paths, verified paths, possible missing targets, and a suggested readback command.
- `verification_hint`
  A short natural-language reminder derived from `task_guidance`.
- `modified_paths`
  The session-level changed-file summary used by the final answer.

When parsing fails, the result also includes `artifacts["parse_error"]` with a structured object:

```json
{
  "kind": "unsupported_or_fallback",
  "summary": "unsupported `|| false` fallback.",
  "message": "parse error: unsupported `|| false` fallback. Supported forms are `|| true`, `|| :`, and `|| help`. Otherwise remove the `|| ...` tail and run the main command directly, or rewrite it as `;` / `&&` explicitly.",
  "suggestion": "Supported forms are `|| true`, `|| :`, and `|| help`. Otherwise remove the `|| ...` tail and run the main command directly, or rewrite it as `;` / `&&` explicitly."
}
```

For multi-file tasks, prefer this workflow:

- read target files first
- make edits
- inspect `task_guidance["verification"]["pending_verification_paths"]`
- run the suggested `cat ...` readback before finishing
- reuse `modified_paths` or `task_guidance["verification"]["changed_paths"]` in the final answer

### 4.5 Flush Runtime State

```python
ok = workspace_handle.flush()
print("flush ok:", ok)
```

Current flush call path:

```text
workspace.flush()
  -> service.flush_workspace(...)
  -> service_ops.file_api.flush_workspace(...)
  -> mirror.checkpoint.resolve_workspace_ref_for_flush(...)
  -> mirror.checkpoint.run_checkpoint_cycle(...)
  -> mirror.checkpoint.flush_workspace_mirror(...)
```

### 4.6 Refresh the Workspace Mirror

Use `refresh(...)` when you want to discard the current runtime mirror and rebuild from the database.

```python
with SessionLocal() as db:
    snapshot = workspace_handle.refresh(db)
    print(snapshot.get("tree") or "")
```

Behavior:

- compare the current mirror with database state
- skip rebuild when they already match
- delete the current mirror and cached snapshot when they differ
- rebuild the mirror from database state only

Notes:

- it does not re-seed `workspace_files`
- its goal is to realign runtime state with database state
- unflushed dirty changes in the mirror are discarded

### 4.7 Access Modes

Workspaces have two explicit access modes:

- `host`
- `agent`

Rules:

- `workspace.bash(...)` requires `agent` mode
- `workspace.write_file(...)` requires `host` mode
- `workspace.read_file(...)` and `workspace.read_directory(...)` are allowed in both modes

Typical sequence:

```python
workspace.ensure(db)
workspace.enter_agent_mode(db)
workspace.bash(db, "cat /workspace/files/demo.md")
workspace.enter_host_mode(db)
conflict = workspace.write_file(db, "/workspace/files/demo.md", "host-side update")
if conflict.get("conflict"):
    workspace.write_file(db, "/workspace/files/demo.md", "host-side update", overwrite=True)
workspace.flush()
```

Overwrite confirmation rules:

- `workspace.write_file(db, path, content, overwrite=False)` does not overwrite an existing file by default
- if the file already exists, it returns a structured conflict payload with `reason="already_exists"` and `requires_confirmation=True`
- shell redirect `>` follows the same rule and fails on existing files
- shell redirect `>|` is the explicit overwrite form
- limited heredoc is supported for stdin-style multi-line writes such as `cat <<'EOF' > /workspace/file ... EOF`
- `help` prints the current shell surface and these write rules inside the agent runtime

### 4.8 Runtime Transaction Semantics

Current internal behavior is organized around two helpers:

- one workspace transaction helper around a full command chain
- one checkpoint-cycle helper around one flush cycle

In practice this means:

- in Redis-backed profiles, file/session/cwd mutations are only considered successful after the runtime state has been written back to Redis
- reads in Redis-backed profiles resolve from Redis-backed runtime state
- `workspace.flush()` resolves the current workspace ref first, then runs one checkpoint cycle
- on the host path, `ensure(db)` prepares the persistence path needed by later `workspace.flush()` calls
- the workspace handle binds to its first real persistence target; request-scoped DB sessions may change, but the underlying database target must stay the same

## 5. Runtime Profiles

### 5.1 `persistent`

Use for durable production environments.

```python
dependencies = build_profile_dependencies(
    settings=settings,
    runtime_profile="persistent",
)
configure_vfs_dependencies(dependencies)
```

Characteristics:

- Redis-backed runtime state
- pgsql repositories
- durable recovery after flush/checkpoint

### 5.2 `ephemeral-local`

Use for local development and demos.

```python
dependencies = build_profile_dependencies(
    settings=settings,
    runtime_profile="ephemeral-local",
)
configure_vfs_dependencies(dependencies)
```

Characteristics:

- in-process runtime state
- in-memory repositories
- no external dependencies

### 5.3 `ephemeral-redis`

Use when multiple instances need shared runtime state without durable persistence.

```python
dependencies = build_profile_dependencies(
    settings=settings,
    runtime_profile="ephemeral-redis",
)
configure_vfs_dependencies(dependencies)
```

Characteristics:

- Redis-backed runtime state
- in-memory repositories
- no PostgreSQL requirement

## 6. Complete Example

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from iruka_vfs import (
    build_profile_dependencies,
    build_workspace_seed,
    configure_vfs_dependencies,
    create_workspace,
)


class Base(DeclarativeBase):
    pass


class AgentWorkspace(Base):
    __tablename__ = "vfs_workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="demo")
    runtime_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Settings:
    default_tenant_id = "demo"
    redis_key_namespace = "iruka-vfs-demo"
    redis_url = "memory://"
    database_url = "sqlite+pysqlite:///:memory:"


dependencies = build_profile_dependencies(
    settings=Settings(),
    runtime_profile="ephemeral-local",
)
configure_vfs_dependencies(dependencies)

engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)

with SessionLocal() as db:
    workspace_row = AgentWorkspace(
        tenant_id="demo",
        runtime_key="workspace:1",
    )
    db.add(workspace_row)
    db.commit()
    db.refresh(workspace_row)

    workspace = create_workspace(
        workspace=workspace_row,
        tenant_id=workspace_row.tenant_id,
        workspace_seed=build_workspace_seed(
            runtime_key=workspace_row.runtime_key,
            tenant_id=workspace_row.tenant_id,
            workspace_files={
                "/workspace/files/demo.md": "hello\n",
            },
        ),
    )

    workspace.ensure(db)
    workspace.enter_agent_mode(db)
    workspace.bash(
        db,
        "edit /workspace/files/demo.md --find hello --replace hello-world",
    )
    workspace.flush()
    print(workspace.read_file(db, "/workspace/files/demo.md"))
```

## 7. Common `VirtualWorkspace` Methods

Common methods are defined in `iruka_vfs/sdk/workspace_handle.py`:

- `ensure(db)`
  Initialize or load the workspace mirror.
- `refresh(db, include_tree=True)`
  Discard the current runtime mirror and rebuild from the database.
- `enter_agent_mode(db)`
  Switch to agent mode.
- `enter_host_mode(db)`
  Switch back to host mode.
- `bash(db, raw_cmd)`
  Run one virtual bash command.
- `read_file(db, path)`
  Read one file.
- `write_file(db, path, content)`
  Write one file directly.
- `read_directory(db, path, recursive=True)`
  Read files under a directory.
