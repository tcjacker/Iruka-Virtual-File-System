# API 升级指南

本文说明如何把 `iruka_vfs` 老版本公开接口迁移到统一后的新接口。

目标很简单：

- 顶层只保留一条推荐路径：`from iruka_vfs import create_workspace`
- 宿主只操作 `VirtualWorkspace`
- 旧接口保留一个兼容窗口，但全部视为 deprecated

兼容窗口：

- deprecated in `0.2`
- removed in `0.3`

## 新主路径

升级后的推荐入口是：

```python
from iruka_vfs import WritableFileSource, create_workspace
```

推荐公开方法是：

- `workspace.ensure(db, *, include_tree=True, available_skills=None)`
- `workspace.run(db, raw_cmd)`
- `workspace.write(db, path, content)`
- `workspace.edit(db, path, old_text, new_text, *, replace_all=False)`
- `workspace.read_file(db, path)`
- `workspace.read_directory(db, path, *, recursive=True)`
- `workspace.file_tree(db, path="/workspace")`
- `workspace.flush()`

## 升级总原则

升级时按这几条规则处理即可：

1. 把 `create_workspace_handle(...)` 统一替换成 `create_workspace(...)`
2. 把 `VirtualWorkspaceHandle` 统一视为 `VirtualWorkspace`
3. 把 `bash / write_file / tool_write / tool_edit` 统一迁到 `run / write / edit`
4. 删除显式 `enter_agent_mode(...)` / `enter_host_mode(...)` / `access_mode(...)` 调用
5. 如果旧代码直接调用 `iruka_vfs.service.*`，改成 `VirtualWorkspace` 高层方法
6. 如果旧代码依赖 `write_file(...)` 的返回 payload，必须同步改返回值消费逻辑

## 接口映射表

| 旧接口 | 新接口 | 说明 |
|---|---|---|
| `iruka_vfs.create_workspace_handle(...)` | `iruka_vfs.create_workspace(...)` | 工厂名收敛，构造参数保持同一路径 |
| `iruka_vfs.VirtualWorkspaceHandle` | `iruka_vfs.VirtualWorkspace` | 公开 handle 名称收敛 |
| `workspace.bash(db, cmd)` | `workspace.run(db, cmd)` | 命令执行入口改名，返回结构保持兼容 |
| `workspace.write_file(db, path, content)` | `workspace.write(db, path, content)` | 整文件写入口收敛，但返回 payload 不再兼容旧 `write_file` |
| `workspace.tool_write(db, path, content)` | `workspace.write(db, path, content)` | 结构化写接口直接收敛到 `write`，返回 payload 保持兼容 |
| `workspace.tool_edit(db, path, old, new, replace_all=False)` | `workspace.edit(db, path, old, new, replace_all=False)` | 结构化编辑接口收敛，返回 payload 保持兼容 |
| `workspace.enter_agent_mode(db)` + `workspace.bash(...)` + `workspace.enter_host_mode(db)` | `workspace.run(db, ...)` | 新路径内部自动处理 mode 切换与恢复 |
| `workspace.enter_host_mode(db)` + `workspace.write_file(...)` | `workspace.write(db, ...)` | 新路径内部自动确保 `host` mode |
| `workspace.access_mode(db)` | 删除 | 不再推荐宿主感知 mode 状态机 |
| `workspace.tree(db)` | `workspace.file_tree(db)` 或 `workspace.ensure(db, include_tree=True)` | 如果只需要树结构，优先用 `file_tree` |

## 顶层导入迁移

### 旧写法

```python
from iruka_vfs import create_workspace_handle, VirtualWorkspaceHandle
```

### 新写法

```python
from iruka_vfs import create_workspace, VirtualWorkspace
```

如果旧代码并没有显式引用 `VirtualWorkspaceHandle` 类型，只需要替换工厂函数即可。

## Workspace 构造迁移

### 旧写法

```python
workspace = create_workspace_handle(
    workspace=workspace_model,
    tenant_id="tenant-a",
    runtime_key="conv:1001",
    primary_file=primary_file,
)
```

### 新写法

```python
workspace = create_workspace(
    workspace=workspace_model,
    tenant_id="tenant-a",
    runtime_key="conv:1001",
    primary_file=primary_file,
)
```

这里的核心变化只有命名收敛，没有新的构造前置条件。

## 动作接口迁移

### 1. 命令执行

#### 旧写法

```python
workspace.enter_agent_mode(db)
result = workspace.bash(db, "cat /workspace/docs/brief.md")
workspace.enter_host_mode(db)
```

#### 新写法

```python
result = workspace.run(db, "cat /workspace/docs/brief.md")
```

返回结构与旧 `bash(...)` 逐项兼容：

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

迁移要点：

- 删除显式 mode 切换
- 直接消费原有 `stdout` / `stderr` / `exit_code`
- 调用结束后 workspace 必须恢复到 `host` mode；如果恢复失败，整个调用视为失败

### 2. 整文件写入

#### 旧写法 A

```python
result = workspace.write_file(db, "/workspace/docs/output.md", "hello")
```

#### 旧写法 B

```python
result = workspace.tool_write(db, "/workspace/docs/output.md", "hello")
```

#### 新写法

```python
result = workspace.write(db, "/workspace/docs/output.md", "hello")
```

`write(...)` 是新的统一整文件写入入口，语义是：

- 文件存在时整文件覆盖
- 文件不存在时自动创建
- 父目录不存在时按当前 VFS 语义自动创建
- 路径必须位于 `/workspace` 下

返回结构采用旧 `tool_write(...)` 的 payload：

```python
{
    "operation": "tool_write",
    "path": str,
    "version": int,
    "created": bool,
    "bytes_written": int,
}
```

这里要特别注意：

- 如果旧代码来自 `tool_write(...)`，通常只需要改方法名
- 如果旧代码来自 `write_file(...)`，除了改方法名，还要改返回值消费逻辑

一个常见改法：

#### 旧写法

```python
version_no = workspace.write_file(db, path, content)["version_no"]
```

#### 新写法

```python
write_result = workspace.write(db, path, content)
version_no = write_result["version"]
```

### 3. 定点文本编辑

#### 旧写法

```python
result = workspace.tool_edit(
    db,
    "/workspace/docs/output.md",
    "hello",
    "hello world",
    replace_all=False,
)
```

#### 新写法

```python
result = workspace.edit(
    db,
    "/workspace/docs/output.md",
    "hello",
    "hello world",
    replace_all=False,
)
```

返回结构与旧 `tool_edit(...)` 逐项兼容：

```python
{
    "operation": "tool_edit",
    "path": str,
    "version": int,
    "replacements": int,
}
```

迁移后仍然保持原有语义：

- 默认必须唯一匹配
- `replace_all=True` 时允许多处替换

### 4. 读取接口

旧代码如果先切 `host` mode 再读文件或目录，可以直接删掉 mode 切换：

#### 旧写法

```python
workspace.enter_host_mode(db)
content = workspace.read_file(db, "/workspace/docs/brief.md")
files = workspace.read_directory(db, "/workspace/docs")
tree = workspace.tree(db)
```

#### 新写法

```python
content = workspace.read_file(db, "/workspace/docs/brief.md")
files = workspace.read_directory(db, "/workspace/docs")
tree = workspace.file_tree(db, "/workspace/docs")
```

说明：

- `read_file(...)` 返回单文件内容字符串
- `read_directory(...)` 返回 `{virtual_path: content}` 映射
- `file_tree(...)` 返回 mirror 上的结构化树
- 这些调用成功返回后，workspace 也必须处于 `host` mode

## `ensure(...)` 的迁移规则

`ensure(...)` 仍然保留，但语义变成“可选预热”：

- 需要提前物化 workspace、预热 tree、或显式拿 bootstrap 结果时，可以继续调用
- 不再要求在每次 `run / write / edit / read_* / file_tree` 前显式调用

推荐理解为：

- `ensure(...)` 是 optional preflight
- 不是新主路径的强制前置步骤

### 旧写法

```python
workspace.ensure(db)
workspace.enter_agent_mode(db)
result = workspace.bash(db, "pwd")
workspace.enter_host_mode(db)
```

### 新写法

```python
workspace.ensure(db)
result = workspace.run(db, "pwd")
```

或者直接：

```python
result = workspace.run(db, "pwd")
```

## `service` facade 的迁移

如果老代码直接调用 `iruka_vfs.service`，建议迁到 `VirtualWorkspace` 高层方法。

| 旧 `service` 接口 | 推荐替代 |
|---|---|
| `service.ensure_virtual_workspace(...)` | `workspace.ensure(...)` |
| `service.bootstrap_workspace_snapshot(...)` | `workspace.bootstrap_snapshot(...)` |
| `service.flush_workspace(...)` | `workspace.flush()` |
| `service.read_workspace_file(...)` | `workspace.read_file(...)` |
| `service.read_workspace_directory(...)` | `workspace.read_directory(...)` |
| `service.render_virtual_tree(...)` | `workspace.file_tree(...)` 或 `workspace.ensure(..., include_tree=True)` |
| `service.run_virtual_bash(...)` | `workspace.run(...)` |
| `service.tool_write_workspace_file(...)` | `workspace.write(...)` |
| `service.tool_edit_workspace_file(...)` | `workspace.edit(...)` |
| `service.write_workspace_file(...)` | `workspace.write(...)` |

说明：

- `iruka_vfs.service` 现在是兼容 facade，不再是推荐接入路径
- 诊断类能力如 `snapshot_virtual_fs_cache_metrics()` 可以继续作为非主路径接口使用
- 不建议新代码继续依赖 `service.get_workspace_access_mode(...)` 或 `service.set_workspace_access_mode(...)`

## 常见升级模板

### 模板 1：最小改动升级

适合想先消除 deprecated warning，再逐步清理调用逻辑的场景。

1. 先把工厂函数替换成 `create_workspace(...)`
2. 再把 `bash / tool_write / tool_edit` 替换成 `run / write / edit`
3. 删除显式 mode 切换
4. 最后检查 `write_file(...)` 旧返回值消费逻辑

### 模板 2：一次性收口升级

适合正在改宿主接入层或 SDK 包装层的场景。

1. 所有入口统一改成 `create_workspace(...)`
2. 所有动作统一改成 `VirtualWorkspace` 高层方法
3. 删除所有 `service.*` 业务调用
4. 把内部封装函数也统一改成 `run / write / edit / read_* / file_tree / flush`

## 升级检查清单

升级完成后，至少检查这几项：

- 不再导入 `create_workspace_handle`
- 不再引用 `VirtualWorkspaceHandle`
- 不再调用 `bash / write_file / tool_write / tool_edit`
- 不再显式调用 `enter_agent_mode / enter_host_mode / access_mode`
- 没有新代码继续走 `iruka_vfs.service.*` 作为主路径
- 所有 `write_file(...)` 旧 payload 消费都已迁到 `write(...)` 的结构化返回值

## 推荐验证方法

如果你在升级宿主接入代码，建议至少验证：

1. `run(...)` 的命令结果消费是否保持原行为
2. `write(...)` 后的文件内容和 `version` 是否符合预期
3. `edit(...)` 的唯一匹配和 `replace_all` 语义是否符合预期
4. `read_file(...)` / `read_directory(...)` / `file_tree(...)` 是否仍能在不显式切 mode 的情况下工作
5. 调用结束后 workspace 是否保持在 `host` mode 的安全默认态

## 一个完整升级示例

### 升级前

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

### 升级后

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
