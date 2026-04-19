# `iruka_vfs`

[English Version](./README.md)

`iruka_vfs` 是一个面向 agent 编辑流程的独立虚拟文件系统运行时。

它负责：

- workspace 运行时状态
- 虚拟文件和目录
- shell 会话与命令日志
- 缓存与 checkpoint 流程

它不负责宿主业务概念，例如 `Conversation`。

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

## 对外 API

推荐使用的入口：

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

## 推荐接入方式

推荐的二方包接入模式是：

1. 在进程启动时完成依赖配置
2. 为一个 agent 创建一个 workspace 对象
3. 绑定一个可写主文件，以及若干只读 context / skill 文件
4. 通过 `workspace.write(...)`、`workspace.edit(...)`、`workspace.read_file(...)`、`workspace.read_directory(...)`、`workspace.file_tree(...)` 和 `workspace.run(...)` 使用统一后的公开路径
5. 在需要先把 workspace 落盘/物化时，把 `workspace.ensure(db)` 作为可选的预检步骤
6. 在明确的持久化边界调用 `workspace.flush()`

更详细的宿主接入说明见 [HOST_ADAPTER.zh-CN.md](/Users/tc/ai/Iruka-Virtual-File-System/HOST_ADAPTER.zh-CN.md)。

## 理想 SDK 形态

```python
from iruka_vfs import WritableFileSource, create_workspace

workspace = create_workspace(
    workspace=workspace_model,
    tenant_id="tenant-a",
    runtime_key="conv:1001",
    primary_file=WritableFileSource(
        file_id="document:123",
        virtual_path="/workspace/files/document_123.md",
        read_text=load_document_text,
        write_text=save_document_text,
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

这个 facade 是轻量对象。它可以在同一个 agent / workspace 身份下跨 turn 复用，但不应该被多个请求并发调用。

## 宿主文件 API

宿主也可以直接通过 Python API 管理虚拟 workspace 内的文件。

- `create_workspace(..., workspace_files={path: content, ...})`
- `workspace.ensure(db)`
- `workspace.write(db, path, content)`
- `workspace.edit(db, path, old_text, new_text)`
- `workspace.read_file(db, path)`
- `workspace.read_directory(db, path, recursive=True)`
- `workspace.file_tree(db, path="/workspace")`
- `workspace.run(db, "...")`
- `workspace.flush()`

## 从弃用 API 迁移

- `workspace.bash(db, cmd)` -> `workspace.run(db, cmd)`
- `workspace.write_file(db, path, content)` -> `workspace.write(db, path, content)`
- `workspace.tool_write(db, path, content)` -> `workspace.write(db, path, content)`
- `workspace.tool_edit(db, path, old_text, new_text)` -> `workspace.edit(db, path, old_text, new_text)`
- 推荐的宿主路径不再需要显式调用 `enter_agent_mode(...)` / `enter_host_mode(...)`

说明：

- 相对路径会自动挂到 `/workspace` 下
- 写文件时会自动创建父目录
- 路径必须位于 `/workspace` 之内
- `file_tree(...)` 会从当前 VFS mirror 返回最新的递归树结构
- `read_directory(...)` 返回 `{virtual_path: content}` 映射
- `write(...)` 是推荐的整文件结构化写入接口，对应类似 Claude Code 的 `write`
- `edit(...)` 是推荐的目标文本结构化编辑接口，对应类似 Claude Code 的 `edit`
- access mode 切换由高层 API 内部处理
- `workspace.ensure(...)` 是可选的预检步骤，用于在需要时物化 workspace 状态

## Workspace 生命周期

建议把一个虚拟 workspace 视为一个 agent 的执行上下文。同一个 workspace id 可以跨多个 turn 复用，但不要从多个请求或 worker 并发调用同一个 `workspace.bash(db, "...")`。

推荐约束：

- 一个 agent 对应一个 workspace
- 同一个 workspace 不并发执行命令
- 数据库 `Session` 按请求创建和传入，不要长期保存在 workspace 对象里
- 在 turn 结束或明确的持久化边界调用 `workspace.flush()`

实践上，最安全的方式是让 workspace 对象只保存标识信息和文件绑定配置，而每次命令执行都使用当前请求的 DB session。

## 本地安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Demo

在仓库根目录执行：

```bash
python examples/standalone_sqlite_demo.py
```

这个 demo 使用：

- 本地 SQLite
- 示例 SQLAlchemy 模型
- 内存版 fake Redis

它会创建一个 workspace，挂载一个可写业务文档文件，通过统一后的公开 API 执行 `cat` 和文本编辑，然后 flush 到宿主文件源。
