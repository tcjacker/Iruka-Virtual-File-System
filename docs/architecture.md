## `iruka_vfs` Architecture

This document describes the current module layout after the package refactor.

## Repository Tree

```text
iruka_vfs/
  __init__.py
  workspace.py
  service.py
  sdk/
    workspace_factory.py
    workspace_handle.py
  service_ops/
    access_mode.py
    bootstrap.py
    file_api.py
    state.py
  runtime/
    executor.py
    fs_commands.py
    editing.py
    filesystem.py
    search.py
    logging_support.py
  mirror/
    context.py
    keys.py
    indexing.py
    serialization.py
    checkpoint.py
  pathing/
    resolution.py
    utils.py
  cache/
    ops.py
    worker.py
  sqlalchemy_repo/
    build.py
    session.py
    workspace.py
    node.py
    command_log.py
  models.py
  repositories.py
  dependencies.py
  dependency_resolution.py
  runtime_seed.py
  file_sources.py
  sqlalchemy_models.py
  configuration.py
  profile_setup.py
  in_memory_repositories.py
  pgsql_repositories.py
  workspace_state_store.py
  workspace_state_serialization.py
  command_runtime.py
  memory_cache.py
  paths.py
  sqlalchemy_repositories.py
  workspace_mirror.py
```

## Layer Overview

```text
Public SDK
  iruka_vfs/__init__.py
  iruka_vfs/workspace.py
  iruka_vfs/service.py

Facade Compatibility
  iruka_vfs/paths.py
  iruka_vfs/memory_cache.py
  iruka_vfs/sqlalchemy_repositories.py
  iruka_vfs/command_runtime.py
  iruka_vfs/workspace_mirror.py
  iruka_vfs/runtime/command_handlers.py

Implementation Layers
  iruka_vfs/sdk/
  iruka_vfs/service_ops/
  iruka_vfs/runtime/
  iruka_vfs/mirror/
  iruka_vfs/pathing/
  iruka_vfs/cache/
  iruka_vfs/sqlalchemy_repo/
```

## Responsibility Split

- `sdk/`
  Owns the public workspace handle and factory used by host applications.

- `service_ops/`
  Owns high-level orchestration:
  workspace bootstrap, host/agent access mode, flush flow, bash entry, and host-side file APIs.

- `runtime/`
  Owns command execution details:
  pipeline execution, filesystem commands, editing commands, search helpers, filesystem mutation helpers, and log shaping.

- `mirror/`
  Owns runtime mirror state:
  active context, key generation, serialization, index rebuild, checkpoint queue, and flush behavior.

- `pathing/`
  Owns path resolution and tree navigation helpers.

- `cache/`
  Owns in-memory content cache and cache flush worker behavior.

- `sqlalchemy_repo/`
  Owns SQLAlchemy-backed repository implementations.

- top-level infrastructure modules
  Own shared configuration and contracts such as dependency injection, repository selection,
  runtime profiles, file source bindings, and workspace-state backend selection.

## Dependency Direction

Preferred dependency direction is:

```text
sdk -> service / service_ops
service_ops -> runtime, mirror, pathing, cache, sqlalchemy_repo
runtime -> pathing, cache
mirror -> sqlalchemy_repo
cache -> models
sqlalchemy_repo -> dependencies, repositories, sqlalchemy models
```

Rules:

- `service.py` is a compatibility bridge, not a place for new implementation.
- New runtime logic should go into the smallest focused submodule first.
- Old flat modules should remain thin facades only, to preserve import compatibility.
- Package `__init__.py` files should re-export stable symbols explicitly through `__all__`.

## Public Entry Points

Stable host-facing entry points are:

- `iruka_vfs.configure_vfs_dependencies(...)`
- `iruka_vfs.create_workspace(...)`
- `workspace.ensure(db)`
- `workspace.enter_agent_mode(db)` / `workspace.enter_host_mode(db)`
- `workspace.bash(db, "...")`
- `workspace.write_file(db, path, content)`
- `workspace.read_file(db, path)`
- `workspace.read_directory(db, path, recursive=True)`
- `workspace.flush()`

## Call Flow

The preferred host integration starts at the package root and then moves through the
workspace handle.

### `create_workspace(...)`

```text
iruka_vfs.create_workspace(...)
  -> iruka_vfs.workspace.create_workspace
  -> iruka_vfs.sdk.workspace_factory.create_workspace_handle(...)
  -> returns iruka_vfs.sdk.workspace_handle.VirtualWorkspace
```

### `workspace.bash(db, "...")`

```text
VirtualWorkspace.bash(...)
  -> iruka_vfs.service.run_virtual_bash(...)
  -> iruka_vfs.integrations.agent.shell.run_virtual_bash(...)
  -> iruka_vfs.mirror.mutation.execute_workspace_mirror_transaction(...)
  -> iruka_vfs.service_ops.access_mode.workspace_access_mode_for_runtime(...)
  -> iruka_vfs.service_ops.bootstrap.ensure_virtual_workspace(...)   # when needed
  -> iruka_vfs.runtime.executor.run_command_chain(...)
  -> iruka_vfs.runtime.executor.run_single_command(...)
  -> iruka_vfs.runtime.fs_commands / editing / search / filesystem
  -> iruka_vfs.service_ops.state.enqueue_virtual_command_log(...)
```

### `workspace.flush()`

```text
VirtualWorkspace.flush()
  -> iruka_vfs.service.flush_workspace(...)
  -> iruka_vfs.service_ops.file_api.flush_workspace(...)
  -> iruka_vfs.mirror.checkpoint.resolve_workspace_ref_for_flush(...)
  -> iruka_vfs.mirror.checkpoint.run_checkpoint_cycle(...)
  -> iruka_vfs.mirror.checkpoint.flush_workspace_mirror(...)
  -> iruka_vfs.mirror.serialization / indexing / context helpers
  -> iruka_vfs.sqlalchemy_repo.* or another selected repository backend
```

## Compatibility Policy

The following modules still exist mainly for backward compatibility:

- `iruka_vfs/service.py`
- `iruka_vfs/paths.py`
- `iruka_vfs/memory_cache.py`
- `iruka_vfs/sqlalchemy_repositories.py`
- `iruka_vfs/command_runtime.py`
- `iruka_vfs/workspace_mirror.py`
- `iruka_vfs/runtime/command_handlers.py`

These files are compatibility facades, not new implementation homes. They keep older
imports working after the refactor by re-exporting symbols from the new subpackages.

New code should prefer the subpackage implementations directly unless the public facade is the intentional integration surface.
