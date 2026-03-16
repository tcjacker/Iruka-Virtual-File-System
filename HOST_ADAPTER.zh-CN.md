# Host Adapter 接入说明

[English Version](./HOST_ADAPTER.md)

`iruka_vfs` 负责 VFS 运行时本身。

宿主服务负责：

- conversations 和 requests
- chapters、documents 等业务源数据
- project 或 domain 状态
- 单次 agent 执行所选择的 runtime

宿主适配层的职责，是把宿主业务对象翻译成 VFS 能理解的 workspace 输入。

## 接入职责

宿主适配层应当：

1. 解析 `tenant_id`、`runtime_key`、源记录 id 等宿主上下文
2. 为一个 agent 构建一个 workspace 对象
3. 把一个可写宿主文件映射成 workspace 的 `primary_file`
4. 把只读 context / skill 数据映射成 `context_files` 和 `skill_files`
5. 在执行命令前调用 `workspace.ensure(db)`
6. 对每条虚拟命令调用 `workspace.bash(db, "...")`
7. 在 turn 结束或其他明确的持久化边界调用 `workspace.flush()`

不要把宿主专有业务模型直接暴露给 VFS API。

## 推荐 API

```python
from iruka_vfs import WritableFileSource, create_workspace

workspace = create_workspace(
    workspace=workspace_model,
    tenant_id=str(workspace_model.tenant_id),
    runtime_key=str(workspace_model.runtime_key),
    primary_file=WritableFileSource(
        file_id=f"chapter:{chapter.id}",
        virtual_path=f"/workspace/chapters/chapter_{chapter.id}.md",
        read_text=lambda: chapter.body_text,
        write_text=lambda text: save_chapter_body(chapter.id, text),
    ),
    workspace_files={
        "/workspace/docs/brief.md": initial_brief_text,
        "notes/host_seed.txt": "seeded by host adapter\n",
    },
    context_files={"outline.md": outline_text},
    skill_files={"style.md": style_text},
)

workspace.ensure(db)
workspace.write_file(db, "/workspace/docs/generated.md", "from host adapter")
brief_text = workspace.read_file(db, "/workspace/docs/brief.md")
doc_files = workspace.read_directory(db, "/workspace/docs")
result = workspace.bash(db, "edit /workspace/chapters/chapter_123.md --find foo --replace bar")
workspace.flush()
```

`RuntimeSeed` 仍然存在于内部实现中，但对宿主侧推荐直接使用 workspace facade。

## 生命周期约束

建议把一个 workspace 视为一个 agent 的单线程执行上下文。workspace 可以跨多个 turn 存续，但同一个 workspace 上的命令执行应保持串行。

必须遵守的约束：

- 不要在同一个 workspace 上并发执行命令
- 不要跨请求或跨线程共享一个活跃的 SQLAlchemy `Session`
- 可复用对象里只保留 workspace 标识和文件绑定，不保留请求级运行时资源
- 调用 `workspace.ensure(db)` 和 `workspace.bash(db, "...")` 时，总是传入当前请求的 DB session
- 把 `workspace.flush()` 作为显式的持久化动作

这样可以在复用 Redis workspace 状态的同时，避免 stale session 和跨请求运行时对象带来的问题。

## 最小映射模型

典型章节场景下的映射关系：

- host conversation/request -> 选择 runtime / workspace
- host chapter/document -> 一个可写 VFS 文件，例如 `/workspace/chapters/chapter_123.md`
- host project state -> `/workspace/context/*.md`
- host skills -> `/workspace/skills/*.md`

## 最小输入要求

适配层至少需要提供：

- `workspace`
- `runtime_key`
- `tenant_id`
- `primary_file`

通常 `primary_file` 应使用 `WritableFileSource`，并提供：

- `virtual_path`
- `read_text()`
- `write_text(text)`

## 实践建议

建议在宿主项目里单独维护一个 `vfs_adapter.py` 或类似模块，把以下逻辑集中到一起：

- workspace 查找
- `create_workspace(...)` 构造
- context / skill 文件映射
- turn 结束时的 `flush()`

业务层只调用 adapter，不直接拼底层参数。
