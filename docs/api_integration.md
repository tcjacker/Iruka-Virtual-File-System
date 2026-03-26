# Iruka VFS API 接入文档

本文档说明如何在业务系统里接入 `iruka_vfs`，并覆盖三种运行模式：

- `persistent`
- `ephemeral-local`
- `ephemeral-redis`

## 1. 核心概念

重构后的 VFS 分成两层：

- `WorkspaceStateStore`
  Agent 运行时直接操作的 workspace 状态层，负责文件树、文件内容、cwd、dirty 状态、锁、checkpoint。
- `VFSRepositories`
  持久化层，负责 workspace/session/node/command_log 的落库或内存实现。

backend 语义补充：

- `ephemeral-local` 下，进程内 mirror 对象本身就是运行态状态。
- Redis profile 下，Redis 是运行态唯一事实来源，进程内 mirror 对象只是单次事务里的短生命周期工作对象。

三种 profile 的映射关系如下：

| Profile | WorkspaceStateStore | VFSRepositories | 适用场景 |
| --- | --- | --- | --- |
| `persistent` | `RedisWorkspaceStateStore` | `pgsql` | 正式环境，支持持久化恢复 |
| `ephemeral-local` | `LocalMemoryStateStore` | `memory` | 单机 demo、本地调试、最轻量接入 |
| `ephemeral-redis` | `RedisWorkspaceStateStore` | `memory` | 多实例共享运行态，但不落数据库 |

三种模式的依赖要求：

| 模式 | 需要 Redis | 需要 PostgreSQL | 数据是否持久化 |
| --- | --- | --- | --- |
| `persistent` | 是 | 是 | 是 |
| `ephemeral-local` | 否 | 否 | 否 |
| `ephemeral-redis` | 是 | 否 | 否 |

## 2. 对外入口

推荐优先使用这几个对外 API：

```python
from iruka_vfs import (
    build_profile_dependencies,
    build_profile_persistent_dependencies,
    build_workspace_seed,
    configure_vfs_dependencies,
    create_workspace,
)
```

它们分别负责：

- `build_profile_dependencies(...)`
  按 profile 组装依赖配置，适合大多数接入场景。
- `build_profile_persistent_dependencies(...)`
  显式构建 `persistent` 模式配置。
- `configure_vfs_dependencies(...)`
  注册当前进程使用的 VFS 依赖。
- `create_workspace(...)`
  创建一个 `VirtualWorkspace` 句柄。
- `build_workspace_seed(...)`
  构造通用 `WorkspaceSeed`。

如果你仍然直接手工构造 `VFSDependencies(...)`，现在默认也会使用内部模型：

```python
from iruka_vfs.dependencies import VFSDependencies, configure_vfs_dependencies


configure_vfs_dependencies(
    VFSDependencies(
        settings=settings,
        runtime_profile="ephemeral-local",
    )
)
```

只有在你显式传 `AgentWorkspace`、`VirtualFileNode`、`VirtualShellSession`、`VirtualShellCommand`、`repositories`、`workspace_state_store` 时，才属于高级自定义接入。

## 2.1 高级自定义参数说明

下面这几个参数都不是普通接入必填项，它们属于高级自定义入口。

`AgentWorkspace`
- 指定 workspace 主模型类。
- repository 和 service 会基于它读写 `id`、`tenant_id`、`runtime_key`、`metadata_json` 等字段。
- 默认已经使用内部 `VFSWorkspace`，只有你要兼容宿主已有 workspace 表时才建议传。

`VirtualFileNode`
- 指定文件树节点 ORM 模型。
- node repository 会基于它读写目录树、文件内容、版本号等。
- 默认已经使用内部 `VFSFileNode`，只有在兼容旧 node 表时才建议传。

`VirtualShellSession`
- 指定 shell session ORM 模型。
- 主要用于记录 session、cwd、env、active 状态。
- 默认已经使用内部 `VFSShellSession`。

`VirtualShellCommand`
- 指定命令日志 ORM 模型。
- command log repository 会把 `raw_cmd`、`stdout_text`、`stderr_text`、`exit_code` 等写进去。
- 默认已经使用内部 `VFSShellCommand`。

`repositories`
- 直接注入整套 `VFSRepositories` 实例。
- 一旦传入，就不会再按 `runtime_profile` 自动构建 `pgsql` 或 `memory` repositories。
- 适合完全自定义持久化层，例如自定义 `PostgresVFSRepositories`、`OssVFSRepositories` 或测试替身。

`workspace_state_store`
- 直接注入 `WorkspaceStateStore` 实例。
- 一旦传入，就不会再按 `runtime_profile` 自动选择 `LocalMemoryStateStore` 或 `RedisWorkspaceStateStore`。
- 适合完全自定义运行态状态层，例如自定义 Redis store、对象存储 store 或测试替身。

推荐原则：

- 普通接入：只传 `settings + runtime_profile`
- 高级自定义：优先传 `repositories` / `workspace_state_store`
- 除非确实需要兼容旧表结构，否则不建议再单独传一组 ORM 模型类

## 3. 接入前提

你的业务侧需要准备：

1. 一个 workspace 业务模型，例如 `AgentWorkspace`
2. 一组 VFS ORM 模型
3. 一个可选的 `load_project_state_payload(...)` 回调

最小要求如下：

```python
from sqlalchemy.orm import Session


def load_project_state_payload(*args, **kwargs) -> dict:
    return {}
```

正式接入时，高层 profile builder 默认直接使用项目中的内部 VFS 模型定义，例如：

- [sqlalchemy_models.py](/Users/tc/ai/Iruka-Virtual-File-System/iruka_vfs/sqlalchemy_models.py)

## 4. 基础接入步骤

### 4.1 配置 profile

最简写法：

```python
from iruka_vfs import build_profile_dependencies, configure_vfs_dependencies


dependencies = build_profile_dependencies(
    settings=settings,
    runtime_profile="ephemeral-local",  # 或 "persistent" / "ephemeral-redis"
)
configure_vfs_dependencies(dependencies)
```

显式 persistent 写法：

```python
from iruka_vfs import build_profile_persistent_dependencies, configure_vfs_dependencies


dependencies = build_profile_persistent_dependencies(settings=settings)
configure_vfs_dependencies(dependencies)
```

如果你需要覆盖内部默认 node/session/command 模型，或覆盖状态加载回调，也可以继续传扩展参数：

```python
dependencies = build_profile_dependencies(
    settings=settings,
    runtime_profile="persistent",
    load_project_state_payload=load_project_state_payload,
)
```

### 4.2 创建 workspace 句柄

推荐先显式构造 `WorkspaceSeed`，再创建句柄。

通用 VFS 用法：

```python
from iruka_vfs import build_workspace_seed, create_workspace


workspace_handle = create_workspace(
    workspace=workspace_row,
    tenant_id=workspace_row.tenant_id,
    workspace_seed=build_workspace_seed(
        runtime_key=workspace_row.runtime_key,
        tenant_id=workspace_row.tenant_id,
        workspace_files={
            "README.md": "# Workspace\n\nDemo workspace.\n",
        },
    ),
)
```

### 4.3 初始化 workspace

```python
with SessionLocal() as db:
    snapshot = workspace_handle.ensure(db)
    print(snapshot.get("tree") or "")
```

host 持久化说明：

- `ensure(db)` 会初始化后续 `workspace.flush()` 所需的 checkpoint 持久化前置条件
- 正常走过一次 `ensure(db)` 后，host 路径不需要再手工准备 checkpoint worker 状态
- 第一次使用真实 DB session 成功执行后，同一个 workspace handle 会绑定这份持久层目标，之后不应再复用到另一套数据库

### 4.4 进入 agent 模式并执行命令

```python
with SessionLocal() as db:
    workspace_handle.enter_agent_mode(db)

    result = workspace_handle.bash(
        db,
        "cd /workspace/files && pwd && cat demo.md",
    )
    print(result["stdout"])
```

虚拟 shell 本身是一个很小的命令子集。当前支持：

- `pwd`
- `cd`
- `ls`
- `cat`
- `rg`
- `grep`
- `wc -l`
- `mkdir`
- `touch`
- `edit`
- `patch`
- `tree`
- `echo`
- `help`

如果 agent 在运行时忘记当前支持哪些命令，可以直接调用 `workspace_handle.bash(db, "help")`，读取其中的命令列表和写入规则。

推荐给 agent 注入的启动 prompt：

```text
你当前处在虚拟 workspace 中，不是完整操作系统 shell。

只能通过 workspace.bash(db, "...") 使用这些命令：
pwd, cd, ls, cat, rg, grep, wc -l, mkdir, touch, edit, patch, tree, echo, help

写入规则：
- 只能写 /workspace 下的路径
- > 不会覆盖已有文件
- >| 才表示显式覆盖
- >> 表示追加
- 多行写文件时可以使用：cat <<'EOF' > /workspace/file ... EOF
- 不要生成真实 shell 扩展语法：||、<、<<<、1>、2>、&>、$(...)、`...`

如果不确定支持什么，先执行：help
```

### 4.5 刷新到后端

```python
ok = workspace_handle.flush()
print("flush ok:", ok)
```

当前 flush 调用链：

```text
workspace.flush()
  -> service.flush_workspace(...)
  -> service_ops.file_api.flush_workspace(...)
  -> mirror.checkpoint.resolve_workspace_ref_for_flush(...)
  -> mirror.checkpoint.run_checkpoint_cycle(...)
  -> mirror.checkpoint.flush_workspace_mirror(...)
```

### 4.6 强制刷新 workspace mirror

如果你怀疑 Redis / 本地缓存里的 workspace mirror 已经过期，或者已经和数据库不一致，可以显式调用：

```python
with SessionLocal() as db:
    snapshot = workspace_handle.refresh(db)
    print(snapshot.get("tree") or "")
```

这个接口的语义是：

- 先比较当前 `WorkspaceStateStore` 中的 mirror 和数据库中的 workspace 状态是否一致
- 如果一致，直接跳过，不做删除重建
- 如果不一致，删除当前 mirror 和 snapshot cache
- 然后仅根据数据库当前状态重建 workspace mirror

注意：

- 它不会重新 seed `workspace_files`
- 它的目标是让运行态 mirror 重新和数据库对齐
- 如果当前 mirror 里有尚未 flush 的脏改动，调用这个接口会丢弃这些未落库修改

## 4.7 访问模式说明

当前 workspace 有两种访问模式：

- `host`
- `agent`

权限规则：

- `workspace.bash(...)` 只能在 `agent` 模式执行
- `workspace.write_file(...)` 只能在 `host` 模式执行
- `workspace.read_file(...)` 和 `workspace.read_directory(...)` 在 `host` / `agent` 两种模式都允许读取

典型流程：

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

覆盖确认规则：

- `workspace.write_file(db, path, content, overwrite=False)` 默认不会覆盖已存在文件
- 如果目标文件已存在，会返回结构化冲突 payload，其中包含 `reason="already_exists"` 和 `requires_confirmation=True`
- shell redirect `>` 也遵循同样规则，遇到已存在文件时失败
- shell redirect `>|` 才表示显式允许覆盖
- 当前支持受限 heredoc，用于 stdin 风格的多行写入，例如 `cat <<'EOF' > /workspace/file ... EOF`
- `help` 会在 agent 运行时打印当前支持的命令面和这些写入规则

### 4.8 运行时事务语义

当前内部主要通过两层 helper 收口：

- 整条命令链的 workspace 事务 helper
- 单次 flush 的 checkpoint cycle helper

接入语义上可以理解为：

- Redis profile 下，文件/cwd/session 等运行态修改，只有成功写回 Redis 后才算成功
- Redis profile 下，读取以 Redis 中的运行态为准
- `workspace.flush()` 会先解析当前 workspace ref，再执行一轮 checkpoint cycle
- host 路径下，`ensure(db)` 会为后续 `workspace.flush()` 准备好持久化路径
- workspace handle 会绑定第一次看到的真实持久层目标；请求级 DB session 可以变化，但底层数据库目标不应变化

## 5. 三种模式示例

### 5.1 persistent

适合正式环境。运行态状态在 Redis，VFS 数据通过 `pgsql` repository 持久化。

```python
dependencies = build_profile_dependencies(
    settings=settings,
    runtime_profile="persistent",
)
configure_vfs_dependencies(dependencies)
```

特点：

- 默认正式模式
- 支持 flush/checkpoint 后恢复
- 适合多实例和长期数据保留

接入要求：

- `settings.redis_url` 可用
- `settings.database_url` 指向 PostgreSQL
- 建议在应用启动时一次性 `configure_vfs_dependencies(...)`

示例：

```python
class Settings:
    default_tenant_id = "prod"
    redis_key_namespace = "iruka-vfs"
    redis_url = "redis://127.0.0.1:6379/0"
    database_url = "postgresql+psycopg://user:pass@127.0.0.1:5432/app"
```

### 5.2 ephemeral-local

适合 demo、单机测试、本地开发。运行态和 repository 都只在当前进程内存中。

```python
dependencies = build_profile_dependencies(
    settings=settings,
    runtime_profile="ephemeral-local",
)
configure_vfs_dependencies(dependencies)
```

特点：

- 不依赖 PostgreSQL
- 不依赖外部 Redis
- 进程退出后数据丢失
- 最适合前期接入 demo

接入要求：

- `settings` 仍然需要提供 `default_tenant_id`
- `redis_url` / `database_url` 可以只是占位值
- 不需要实际 Redis / PostgreSQL 服务

示例：

```python
class Settings:
    default_tenant_id = "demo"
    redis_key_namespace = "iruka-vfs-demo"
    redis_url = "memory://"
    database_url = "sqlite+pysqlite:///:memory:"
```

### 5.3 ephemeral-redis

适合不想落数据库，但希望多个 worker 共享运行态的场景。

```python
dependencies = build_profile_dependencies(
    settings=settings,
    runtime_profile="ephemeral-redis",
)
configure_vfs_dependencies(dependencies)
```

特点：

- 运行态在 Redis
- repository 仍为 memory
- 不做数据库持久化
- 适合共享会话型 demo

接入要求：

- `settings.redis_url` 可用
- `database_url` 可以只是占位值
- 不需要 PostgreSQL

示例：

```python
class Settings:
    default_tenant_id = "demo"
    redis_key_namespace = "iruka-vfs-demo"
    redis_url = "redis://127.0.0.1:6379/0"
    database_url = "sqlite+pysqlite:///:memory:"
```

## 6. 完整接入示例

下面是一段可直接参考的最小接入代码：

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from iruka_vfs import (
    build_workspace_seed,
    build_profile_dependencies,
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
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    current_objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    focus_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Settings:
    default_tenant_id = "demo"
    redis_key_namespace = "iruka-vfs-demo"
    redis_url = "memory://"
    database_url = "sqlite+pysqlite:///:memory:"


def load_project_state_payload(*args, **kwargs) -> dict:
    return {}


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
        project_id=1,
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

## 7. 常用 API

`VirtualWorkspace` 常用方法见 [workspace_handle.py](/Users/tc/ai/Iruka-Virtual-File-System/iruka_vfs/sdk/workspace_handle.py)：

- `ensure(db)`
  初始化或加载 workspace mirror
- `refresh(db, include_tree=True)`
  丢弃当前运行态 mirror，并从数据库重建
- `enter_agent_mode(db)`
  切到 agent 写模式
- `enter_host_mode(db)`
  切回 host 模式
- `bash(db, raw_cmd)`
  执行虚拟 bash 命令
- `read_file(db, path)`
  读取文件内容
- `write_file(db, path, content)`
  直接写文件
- `read_directory(db, path, recursive=True)`
  读取目录
- `flush()`
  把当前 dirty 状态 flush 到后端

## 8. 命令示例

```python
workspace.bash(db, "ls /workspace/files")
workspace.bash(db, "cat /workspace/files/demo.md")
workspace.bash(db, "mkdir -p /workspace/files/docs")
workspace.bash(db, "touch /workspace/files/docs/notes.md")
workspace.bash(
    db,
    "edit /workspace/files/demo.md --find hello --replace hello-world",
)
workspace.bash(
    db,
    "patch --path /workspace/files/demo.md --find hello-world --replace final-text",
)
```

## 9. Redis / 内存 / pgsql 接入说明

### 9.1 Redis

Redis 在当前架构里是 `WorkspaceStateStore` 的实现载体，不只是旁路缓存。

它主要承载：

- workspace mirror
- workspace 锁
- dirty / checkpoint 调度
- `ephemeral-redis` 的共享运行态
- `persistent` 的运行态状态层

接入时至少要保证：

- `settings.redis_url` 正确
- `settings.redis_key_namespace` 唯一
- 多实例场景下使用同一个 namespace

### 9.2 本机内存

本机内存模式对应 `LocalMemoryStateStore + InMemoryRepositories`。

特点：

- 无外部依赖
- 单进程有效
- 重启即丢失
- 最适合 demo / 单测 / 本地开发

### 9.3 pgsql

`pgsql` 对应持久化 repository backend。

它承载：

- workspace metadata
- virtual file nodes
- shell sessions
- command logs

推荐：

- 仅在 `persistent` 模式使用
- 使用 PostgreSQL，而不是把这条路径当成 SQLite demo backend

## 10. 模式选择建议

- 需要正式持久化、可恢复、可长期保留数据：`persistent`
- 只想快速接 demo，不想配数据库和 Redis：`ephemeral-local`
- 想要共享运行态，但仍不想落 PostgreSQL：`ephemeral-redis`

## 11. 相关文件

- [__init__.py](/Users/tc/ai/Iruka-Virtual-File-System/iruka_vfs/__init__.py)
- [profile_setup.py](/Users/tc/ai/Iruka-Virtual-File-System/iruka_vfs/profile_setup.py)
- [workspace_handle.py](/Users/tc/ai/Iruka-Virtual-File-System/iruka_vfs/sdk/workspace_handle.py)
- [workspace_state_store.py](/Users/tc/ai/Iruka-Virtual-File-System/iruka_vfs/workspace_state_store.py)
- [pgsql_repositories.py](/Users/tc/ai/Iruka-Virtual-File-System/iruka_vfs/pgsql_repositories.py)
- [in_memory_repositories.py](/Users/tc/ai/Iruka-Virtual-File-System/iruka_vfs/in_memory_repositories.py)
- [standalone_sqlite_demo.py](/Users/tc/ai/Iruka-Virtual-File-System/examples/standalone_sqlite_demo.py)
