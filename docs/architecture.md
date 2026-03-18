## `iruka_vfs` Architecture

This document describes the current module layout after the package refactor.

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

## Compatibility Policy

The following modules still exist mainly for backward compatibility:

- `iruka_vfs/service.py`
- `iruka_vfs/paths.py`
- `iruka_vfs/memory_cache.py`
- `iruka_vfs/sqlalchemy_repositories.py`
- `iruka_vfs/command_runtime.py`
- `iruka_vfs/workspace_mirror.py`
- `iruka_vfs/runtime/command_handlers.py`

New code should prefer the subpackage implementations directly unless the public facade is the intentional integration surface.
