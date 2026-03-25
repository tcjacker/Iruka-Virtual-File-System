# `iruka_vfs`

[English Version](./README.md)

`iruka_vfs` 是一个面向 agent 编辑流程的独立虚拟文件系统运行时。

它负责：

- workspace 运行时状态
- 虚拟文件和目录
- shell 会话与命令日志
- 缓存与 checkpoint 流程

它不负责宿主业务概念，例如 `Conversation`。

## 快速开始

推荐先看两份文档：

- 架构分层：[docs/architecture.md](/Users/tc/ai/Iruka-Virtual-File-System/docs/architecture.md)
- API 接入与三种模式：[docs/api_integration.md](/Users/tc/ai/Iruka-Virtual-File-System/docs/api_integration.md)

如果你只关心怎么接，优先看 `docs/api_integration.md`。

## 三种运行模式

当前推荐的运行模式如下：

| 模式 | WorkspaceStateStore | VFSRepositories | 依赖 | 适用场景 |
| --- | --- | --- | --- | --- |
| `persistent` | Redis | pgsql | Redis + PostgreSQL | 正式环境、可恢复、可持久化 |
| `ephemeral-local` | 本机内存 | memory | 无外部依赖 | 本地开发、demo、最轻量接入 |
| `ephemeral-redis` | Redis | memory | Redis | 多实例共享运行态、但不落库 |

推荐选择：

- 要正式持久化：`persistent`
- 要最轻量 demo：`ephemeral-local`
- 要共享运行态但不想落库：`ephemeral-redis`

## 仓库结构

```text
iruka_vfs_repo/
  iruka_vfs/
  examples/
  tests/
  docs/
  README.md
  README.zh-CN.md
  HOST_ADAPTER.md
  HOST_ADAPTER.zh-CN.md
  pyproject.toml
```

当前包分层和依赖方向见 [docs/architecture.md](/Users/tc/ai/Iruka-Virtual-File-System/docs/architecture.md)。

这轮重构后，项目结构可以理解为：

- 对外入口：`iruka_vfs/__init__.py`、`iruka_vfs/workspace.py`
- workspace facade 和工厂：`iruka_vfs/sdk/`
- 编排层：`iruka_vfs/service_ops/`
- 执行层：`iruka_vfs/runtime/`
- mirror / pathing / cache / repository 内部实现：`iruka_vfs/mirror/`、`iruka_vfs/pathing/`、`iruka_vfs/cache/`、`iruka_vfs/sqlalchemy_repo/`
- 为兼容旧 import 保留的 facade：`service.py`、`command_runtime.py`、`memory_cache.py`、`paths.py`、`sqlalchemy_repositories.py`、`workspace_mirror.py`

## 对外 API

推荐使用的入口：

- `iruka_vfs.build_profile_dependencies(...)`
- `iruka_vfs.build_profile_persistent_dependencies(...)`
- `iruka_vfs.configure_vfs_dependencies(...)`
- `iruka_vfs.create_workspace(...)`
- `workspace.ensure(db)`
- `workspace.bash(db, "...")`
- `workspace.flush()`
- `iruka_vfs.service.snapshot_virtual_fs_cache_metrics()`

最小接入示例：

```python
from iruka_vfs import build_profile_dependencies, configure_vfs_dependencies

configure_vfs_dependencies(
    build_profile_dependencies(
        settings=settings,
        runtime_profile="ephemeral-local",
    )
)
```

## 推荐接入方式

推荐的二方包接入模式是：

1. 在进程启动时完成依赖配置
2. 为一个 agent 创建一个 workspace 对象
3. 通过 `workspace_files` 注入初始文件
4. 通过 `workspace.bash(db, "...")` 执行命令
5. 在明确的持久化边界调用 `workspace.flush()`

更详细说明见：

- 宿主侧适配契约：[HOST_ADAPTER.zh-CN.md](/Users/tc/ai/Iruka-Virtual-File-System/HOST_ADAPTER.zh-CN.md)
- API / Redis / 内存 / pgsql 接入：[docs/api_integration.md](/Users/tc/ai/Iruka-Virtual-File-System/docs/api_integration.md)

## Agent 接入路径

推荐的 Agent 接入方式是：

1. 进程启动时调用 `configure_vfs_dependencies(...)`
2. 为一个 agent / workspace 构造一个 `VirtualWorkspace`
3. 在执行命令前调用 `workspace.ensure(db)`
4. 调用 `workspace.enter_agent_mode(db)` 切到 agent 模式
5. 用 `workspace.bash(db, "...")` 执行虚拟命令
6. 宿主直读直写前切回 `workspace.enter_host_mode(db)`
7. 在 turn 结束或明确持久化边界调用 `workspace.flush()`

对 host 路径来说，`workspace.ensure(db)` 也会顺带初始化 `workspace.flush()` 所需的 checkpoint 持久化前置条件。正常先执行一次 `ensure(db)` 后，宿主不需要再手工初始化 checkpoint worker 状态。

核心调用链如下：

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

## 理想 SDK 形态

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

这个 facade 是轻量对象。它可以在同一个 agent / workspace 身份下跨 turn 复用，但不应该被多个请求并发调用。

在 Redis profile 下，Redis 是运行态唯一事实来源。进程内 mirror 对象只是在单次事务或单条命令链期间使用的短生命周期工作对象。
在 host 路径上，一次成功的 `ensure(db)` 也会为后续 `workspace.flush()` 准备好 checkpoint 持久化路径。
同一个 workspace handle 还会在第一次看到真实持久层目标后绑定这份目标；首次成功 `ensure/read/write/bash` 之后，不应再把这个 handle 切到另一套数据库。

## 宿主文件 API

除了 `workspace.bash(...)` 以外，宿主也可以直接通过 Python API 管理虚拟 workspace 内的文件。

- `create_workspace(..., workspace_files={path: content, ...})`
- `workspace.write_file(db, path, content, overwrite=False)`
- `workspace.read_file(db, path)`
- `workspace.read_directory(db, path, recursive=True)`
- `workspace.enter_agent_mode(db)` / `workspace.enter_host_mode(db)`

当前访问模式说明：

- 相对路径会自动挂到 `/workspace` 下
- 写文件时会自动创建父目录
- 路径必须位于 `/workspace` 之内
- `read_directory(...)` 返回 `{virtual_path: content}` 映射
- `write_file(...)` 只能在 `host` 模式使用
- `write_file(...)` 只有在显式传 `overwrite=True` 时才会覆盖已有文件
- `read_file(...)`、`read_directory(...)` 在 `host` 和 `agent` 模式都可读
- `workspace.bash(...)` 只能在 `agent` 模式使用

覆盖确认规则：

- `workspace.write_file(...)` 在目标文件已存在且 `overwrite=False` 时，会返回结构化冲突 payload
- shell redirect `>` 在目标文件已存在时也会失败，并返回结构化冲突 payload
- shell redirect `>|` 才表示显式允许覆盖

## Workspace 生命周期

建议把一个虚拟 workspace 视为一个 agent 的执行上下文。同一个 workspace id 可以跨多个 turn 复用，但不要从多个请求或 worker 并发调用同一个 `workspace.bash(db, "...")`。

推荐约束：

- 一个 agent 对应一个 workspace
- 同一个 workspace 不并发执行命令
- 数据库 `Session` 按请求创建和传入，不要长期保存在 workspace 对象里
- 同一个 workspace handle 的持久层目标应保持稳定；首次使用后不要再切换到另一套数据库
- 在 turn 结束或明确的持久化边界调用 `workspace.flush()`

实践上，最安全的方式是让 workspace 对象只保存标识信息和 seed 配置，而每次命令执行都使用当前请求的 DB session。

## 本地安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Demo 与测试

在仓库根目录执行：

```bash
python examples/standalone_sqlite_demo.py
```

这个 demo 使用：

- 本地 SQLite
- 示例 SQLAlchemy 模型
- 内存版 fake Redis

它会创建一个 workspace，注入初始文件，执行 `cat` 和 `edit`，然后 flush。

更完整的页面 demo：

```bash
python examples/vfs_web_demo.py --host 127.0.0.1 --port 8765
```

页面支持切换：

- `persistent`
- `ephemeral-local`
- `ephemeral-redis`
