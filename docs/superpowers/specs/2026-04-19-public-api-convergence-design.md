# Public API Convergence Design

## Goal

收口 `iruka_vfs` 对外 API，只保留一条推荐公开路径：

- 顶层入口只负责依赖配置和 workspace handle 构造
- `VirtualWorkspace` 成为唯一推荐的宿主侧操作入口
- 旧入口保留一个版本周期作为兼容层，但明确标记 deprecated

这次设计的目标不是重写底层 runtime，而是在不推翻现有实现的前提下，完成对外接口边界的收敛。

## Current Problems

当前公开面同时存在三层：

1. 顶层 `iruka_vfs`
2. `iruka_vfs.service`
3. `iruka_vfs.service_ops.*`

README 和 `HOST_ADAPTER.md` 已经倾向推荐 workspace handle，但真实代码里仍然保留多条并行入口，导致以下问题：

- 文档推荐路径和真实可见公开面不一致
- 调用方会把 `iruka_vfs.service` 误当成稳定公开 API
- `agent/host` access mode 作为显式状态机暴露给调用方，接口层级过低，容易误用
- `create_workspace` / `create_workspace_handle`、`VirtualWorkspace` / `VirtualWorkspaceHandle`、`write_file` / `tool_write` / `tool_edit` 并存，主路径不清晰

## Non-Goals

本轮不做以下事项：

- 不删除旧接口，只做一版兼容保留
- 不重写 `service_ops` 和 runtime 内部实现
- 不统一所有返回值为 dataclass 或 typed model
- 不改变 VFS 的持久化、镜像、锁、flush 等内部机制

## Recommended Public API

### Top-Level Exports

`iruka_vfs` 顶层推荐公开面收敛为：

- `configure_vfs_dependencies`
- `ExternalFileSource`
- `WritableFileSource`
- `VirtualWorkspace`
- `create_workspace`

其中：

- `create_workspace` 是唯一推荐构造入口
- `VirtualWorkspace` 是唯一推荐操作入口
- `create_workspace_handle` 和 `VirtualWorkspaceHandle` 不再出现在推荐公开面中

### Workspace Handle API

`create_workspace(...)` 返回 `VirtualWorkspace`，其推荐公开方法为：

- `ensure(db, *, include_tree=True, available_skills=None)`
- `run(db, raw_cmd)`
- `write(db, path, content)`
- `edit(db, path, old_text, new_text, *, replace_all=False)`
- `read_file(db, path)`
- `read_directory(db, path, *, recursive=True)`
- `file_tree(db, path="/workspace")`
- `flush()`

这些方法的职责定义如下。

#### `ensure(db, *, include_tree=True, available_skills=None)`

负责保证 workspace 已完成 runtime bootstrap。该方法继续保留，作为宿主在执行命令前的显式准备动作。

#### `run(db, raw_cmd)`

这是宿主侧唯一推荐的命令执行入口，用于触发虚拟 shell 风格命令。

设计要求：

- 对调用方隐藏 access mode 切换细节
- 在执行前确保 workspace 处于 `agent` mode
- 执行完成后将 workspace 恢复为 `host` mode
- 若原状态已是 `host`，最终状态必须仍为 `host`
- 若运行中抛出异常，也必须尝试恢复到 `host` mode

公开语义为“执行命令”，而不是“切换模式后执行命令”。

#### `write(db, path, content)`

这是宿主侧唯一推荐的整文件写入入口。

设计要求：

- 语义等价于当前 `write_file(...)` / `tool_write(...)` 中更适合宿主的一条
- 对调用方隐藏 access mode 切换细节
- 在执行前确保 workspace 处于 `host` mode
- 返回值延续当前结构化写入返回风格，避免本轮引入二次迁移

推荐替代关系：

- `write_file(...)` -> `write(...)`
- `tool_write(...)` -> `write(...)`

#### `edit(db, path, old_text, new_text, *, replace_all=False)`

这是宿主侧唯一推荐的定点文本替换入口。

设计要求：

- 语义承接当前 `tool_edit(...)`
- 保持“默认必须唯一匹配，除非 `replace_all=True`”的约束
- 对调用方隐藏 access mode 切换细节
- 在执行前确保 workspace 处于 `host` mode

#### `read_file(db, path)` / `read_directory(db, path, *, recursive=True)` / `file_tree(db, path="/workspace")`

这些继续保留为宿主读取入口，但不再要求调用方显式切换到 `host` mode。

设计要求：

- 这些 API 一律作为“宿主侧读取动作”对外表述
- 实现上统一确保在 `host` mode 下执行
- `file_tree(...)` 保持返回 mirror 上的最新结构化树

#### `flush()`

继续保留为显式 durability boundary。

这次不改变：

- flush 的同步语义
- 只接受 `workspace_id` + tenant 标识进行落盘的内部实现

## Access Mode Strategy

### Public Rule

access mode 不再作为推荐公开 API 的一部分。外部调用方不需要知道何时进入 `agent` 或 `host` mode。

### Internal Rule

mode 仍然保留在内部实现中，用于维持现有安全边界：

- `run(...)` 走 `agent` mode
- `write(...)` / `edit(...)` / `read_*` / `file_tree(...)` 走 `host` mode

### Recovery Rule

高层动作 API 必须对 mode 恢复负责：

- 成功执行后恢复到 `host`
- 失败执行后也尝试恢复到 `host`
- 恢复失败时，应优先抛出原始业务异常，但需要保留恢复失败信息用于排查

本轮实现可以采用保守方案：

- 若动作前已在 `host`，动作后显式切回 `host`
- 若动作前已在 `agent`，动作后仍显式切回 `host`

这样会牺牲“保留原模式”的灵活性，但能换来宿主侧接口的稳定和简单。对宿主来说，完成一次动作后 workspace 回到 `host` 是更安全的默认行为。

## Deprecation Plan

以下旧入口保留一个版本周期，但全部标记 deprecated：

- `iruka_vfs.service`
- `iruka_vfs.create_workspace_handle`
- `iruka_vfs.VirtualWorkspaceHandle`
- `VirtualWorkspace.bash(...)`
- `VirtualWorkspace.write_file(...)`
- `VirtualWorkspace.tool_write(...)`
- `VirtualWorkspace.tool_edit(...)`
- `VirtualWorkspace.enter_agent_mode(...)`
- `VirtualWorkspace.enter_host_mode(...)`
- `VirtualWorkspace.access_mode(...)`
- `VirtualWorkspace.tree(...)`

标记方式：

- 运行时发出 `DeprecationWarning`
- docstring 中给出替代接口
- 文档主路径不再出现这些接口

## Migration Rules

迁移说明需要在 README 和 `HOST_ADAPTER.md` 中写清楚，至少包含以下映射：

- `workspace.bash(db, cmd)` -> `workspace.run(db, cmd)`
- `workspace.write_file(db, path, content)` -> `workspace.write(db, path, content)`
- `workspace.tool_write(db, path, content)` -> `workspace.write(db, path, content)`
- `workspace.tool_edit(db, path, old_text, new_text)` -> `workspace.edit(db, path, old_text, new_text)`
- `workspace.enter_agent_mode(db); workspace.bash(db, cmd); workspace.enter_host_mode(db)` -> `workspace.run(db, cmd)`
- `workspace.tree(db)` -> `workspace.ensure(db, include_tree=True)` or `workspace.file_tree(db)`

## Module Boundary Changes

### `iruka_vfs.__init__`

需要收紧 `__all__`，仅保留推荐公开面。

兼容别名仍可存在于模块内，但不应继续出现在顶层推荐导出列表中。

### `iruka_vfs.service`

该模块保留一版兼容，但角色改为“deprecated compatibility facade”，不再是推荐集成入口。

要求：

- 模块文档开头明确说明已 deprecated
- 对主要旧导出补迁移说明
- README 不再把 `iruka_vfs.service.snapshot_virtual_fs_cache_metrics()` 列为主路径的一部分

若需要保留缓存指标能力，应在文档中作为附加运行时诊断能力描述，而不是主 API。

### `iruka_vfs.sdk`

SDK 目录继续存在，但对外主文档不再强调它是独立入口；推荐用法统一写成 `from iruka_vfs import create_workspace`。

## Testing Strategy

本轮实现至少需要覆盖以下测试。

### New API Tests

- `workspace.run(...)` 会委托到底层命令执行逻辑
- `workspace.run(...)` 会自动切换到 agent mode 并在结束后恢复 host mode
- `workspace.write(...)` 映射到结构化写入逻辑
- `workspace.edit(...)` 映射到结构化编辑逻辑
- `workspace.read_file(...)`、`workspace.read_directory(...)`、`workspace.file_tree(...)` 继续工作

### Deprecation Tests

- 旧方法仍可调用
- 旧方法调用时发出 `DeprecationWarning`
- 旧别名导入仍可用，但不再位于推荐 `__all__`

### Documentation Consistency Checks

需要更新并人工核对：

- `README.md`
- `README.zh-CN.md`
- `HOST_ADAPTER.md`
- `HOST_ADAPTER.zh-CN.md`
- `examples/standalone_sqlite_demo.py`

文档中的示例必须全部改走新主路径。

## Error Handling

高层 API 的错误语义保持尽量稳定：

- 命令执行失败：保持当前 `run_virtual_bash(...)` 的结果结构
- 路径越界：继续抛 `PermissionError`
- 目标不存在：继续抛 `FileNotFoundError`
- 编辑匹配失败或多匹配：继续抛 `ValueError`

本轮不重新设计异常类型层级。

## Implementation Notes

建议通过在 `VirtualWorkspace` 内新增高层方法并让旧方法转发到新方法的方式实现收口：

- 新方法成为主实现入口
- 旧方法只做 warning + 转发
- `enter_*_mode` 系列保留为兼容层，不再出现在推荐示例中

为避免在每个方法里散落重复逻辑，可以在 `VirtualWorkspace` 内加入一个私有 helper，用于：

- 切换到目标 mode
- 执行动作
- 在 `finally` 中恢复到 `host`

这个 helper 属于内部实现细节，不应成为公开 API。

## Acceptance Criteria

完成后应满足：

1. 新用户只看顶层 README，就只能看到一条公开主路径
2. 调用方不需要显式处理 access mode 即可完成常见宿主操作
3. 旧入口仍能工作一个版本周期，但调用时会收到明确 deprecated 提示
4. 顶层推荐导出面与文档推荐路径保持一致
5. 示例和宿主接入文档全部切换到新 API
