# API Upgrade Guide

This guide shows how to migrate older `iruka_vfs` public APIs onto the converged public path.

The target shape is:

- one recommended top-level constructor: `from iruka_vfs import create_workspace`
- one recommended host handle: `VirtualWorkspace`
- one version window of deprecated compatibility shims

Compatibility window:

- deprecated in `0.2`
- removed in `0.3`

## Recommended Public Path

```python
from iruka_vfs import WritableFileSource, create_workspace
```

Recommended methods on the returned workspace handle:

- `workspace.ensure(db, *, include_tree=True, available_skills=None)`
- `workspace.run(db, raw_cmd)`
- `workspace.write(db, path, content)`
- `workspace.edit(db, path, old_text, new_text, *, replace_all=False)`
- `workspace.read_file(db, path)`
- `workspace.read_directory(db, path, *, recursive=True)`
- `workspace.file_tree(db, path="/workspace")`
- `workspace.flush()`

## Migration Rules

Use these rules to migrate older code:

1. Replace `create_workspace_handle(...)` with `create_workspace(...)`
2. Treat `VirtualWorkspaceHandle` as `VirtualWorkspace`
3. Replace `bash / write_file / tool_write / tool_edit` with `run / write / edit`
4. Delete explicit `enter_agent_mode(...)` / `enter_host_mode(...)` / `access_mode(...)` calls
5. Route old `iruka_vfs.service.*` business calls through high-level `VirtualWorkspace` methods
6. Update payload consumers if the old code depended on `write_file(...)` return values

## Old-To-New Mapping

| Old API | New API | Notes |
|---|---|---|
| `iruka_vfs.create_workspace_handle(...)` | `iruka_vfs.create_workspace(...)` | Factory name convergence |
| `iruka_vfs.VirtualWorkspaceHandle` | `iruka_vfs.VirtualWorkspace` | Public handle name convergence |
| `workspace.bash(db, cmd)` | `workspace.run(db, cmd)` | Command result payload stays compatible |
| `workspace.write_file(db, path, content)` | `workspace.write(db, path, content)` | Full-file write converges, but payload is no longer compatible with old `write_file` |
| `workspace.tool_write(db, path, content)` | `workspace.write(db, path, content)` | Structured write payload remains compatible |
| `workspace.tool_edit(db, path, old, new, replace_all=False)` | `workspace.edit(db, path, old, new, replace_all=False)` | Structured edit payload remains compatible |
| `workspace.enter_agent_mode(db); workspace.bash(...); workspace.enter_host_mode(db)` | `workspace.run(db, ...)` | High-level API owns mode transitions and recovery |
| `workspace.access_mode(db)` | remove | Host integrations should not depend on the mode state machine |
| `workspace.tree(db)` | `workspace.file_tree(db)` or `workspace.ensure(db, include_tree=True)` | Prefer `file_tree` when you need the tree directly |

## Payload Compatibility

`run(...)` keeps the current `bash(...)` payload shape:

```python
{
    "session_id": int,
    "command_id": int,
    "stdout": str,
    "stderr": str,
    "exit_code": int,
    "artifacts": dict,
    "cwd": str,
}
```

`write(...)` uses the structured `tool_write(...)` payload:

```python
{
    "operation": "tool_write",
    "path": str,
    "version": int,
    "created": bool,
    "bytes_written": int,
}
```

`edit(...)` keeps the current `tool_edit(...)` payload:

```python
{
    "operation": "tool_edit",
    "path": str,
    "version": int,
    "replacements": int,
}
```

Important: code that previously depended on `write_file(...)` payload details must update that payload handling during migration.

## Common Code Upgrades

### Constructor

Old:

```python
from iruka_vfs import create_workspace_handle

workspace = create_workspace_handle(...)
```

New:

```python
from iruka_vfs import create_workspace

workspace = create_workspace(...)
```

### Command execution

Old:

```python
workspace.enter_agent_mode(db)
result = workspace.bash(db, "cat /workspace/docs/brief.md")
workspace.enter_host_mode(db)
```

New:

```python
result = workspace.run(db, "cat /workspace/docs/brief.md")
```

### Full-file write

Old:

```python
result = workspace.write_file(db, "/workspace/docs/output.md", "hello")
```

New:

```python
result = workspace.write(db, "/workspace/docs/output.md", "hello")
version_no = result["version"]
```

### Structured edit

Old:

```python
result = workspace.tool_edit(db, path, "hello", "hello world")
```

New:

```python
result = workspace.edit(db, path, "hello", "hello world")
```

### Read path

Old:

```python
workspace.enter_host_mode(db)
content = workspace.read_file(db, path)
files = workspace.read_directory(db, "/workspace/docs")
tree = workspace.tree(db)
```

New:

```python
content = workspace.read_file(db, path)
files = workspace.read_directory(db, "/workspace/docs")
tree = workspace.file_tree(db, "/workspace/docs")
```

## `ensure(...)` Is Optional

`ensure(...)` remains public, but it is now an optional preflight step.

Use it when you want to:

- materialize the workspace early
- warm the tree snapshot
- fetch bootstrap metadata explicitly

Do not treat it as a required step before every `run / write / edit / read_* / file_tree` call.

## `service` Facade Migration

If older integration code still calls `iruka_vfs.service`, move those calls to `VirtualWorkspace` methods:

| Old `service` API | Recommended replacement |
|---|---|
| `service.ensure_virtual_workspace(...)` | `workspace.ensure(...)` |
| `service.bootstrap_workspace_snapshot(...)` | `workspace.bootstrap_snapshot(...)` |
| `service.flush_workspace(...)` | `workspace.flush()` |
| `service.read_workspace_file(...)` | `workspace.read_file(...)` |
| `service.read_workspace_directory(...)` | `workspace.read_directory(...)` |
| `service.render_virtual_tree(...)` | `workspace.file_tree(...)` or `workspace.ensure(..., include_tree=True)` |
| `service.run_virtual_bash(...)` | `workspace.run(...)` |
| `service.tool_write_workspace_file(...)` | `workspace.write(...)` |
| `service.tool_edit_workspace_file(...)` | `workspace.edit(...)` |
| `service.write_workspace_file(...)` | `workspace.write(...)` |

The `service` module is now a deprecated compatibility facade, not the recommended integration surface.

## Upgrade Checklist

- no imports of `create_workspace_handle`
- no references to `VirtualWorkspaceHandle`
- no calls to `bash / write_file / tool_write / tool_edit`
- no explicit `enter_agent_mode / enter_host_mode / access_mode`
- no new business-path use of `iruka_vfs.service.*`
- all old `write_file(...)` payload consumers updated to `write(...)`

## Full Example

Before:

```python
from iruka_vfs import create_workspace_handle

workspace = create_workspace_handle(
    workspace=workspace_model,
    tenant_id="tenant-a",
    runtime_key="conv:1001",
    primary_file=primary_file,
)

workspace.ensure(db)
workspace.enter_agent_mode(db)
workspace.bash(db, "cat /workspace/docs/brief.md")
workspace.enter_host_mode(db)
workspace.write_file(db, "/workspace/docs/output.md", "hello")
workspace.tool_edit(db, "/workspace/docs/output.md", "hello", "hello world")
tree = workspace.tree(db)
workspace.flush()
```

After:

```python
from iruka_vfs import create_workspace

workspace = create_workspace(
    workspace=workspace_model,
    tenant_id="tenant-a",
    runtime_key="conv:1001",
    primary_file=primary_file,
)

workspace.ensure(db)
workspace.run(db, "cat /workspace/docs/brief.md")
workspace.write(db, "/workspace/docs/output.md", "hello")
workspace.edit(db, "/workspace/docs/output.md", "hello", "hello world")
tree = workspace.file_tree(db, "/workspace")
workspace.flush()
```
