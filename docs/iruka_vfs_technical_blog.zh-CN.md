# 基于 `iruka_vfs` 的 Agent 虚拟文件系统设计与实现

## 一、为什么要做一个 Agent 专用的 VFS Runtime

在很多 AI Agent 场景里，模型并不只是“生成一段文本”，而是在一个持续存在的工作空间里完成一系列操作：

- 读取当前工作文件内容
- 查看宿主注入的参考资料
- 执行 `cat`、`ls`、`grep`、`edit`、`patch` 等命令
- 在多轮交互中维持 `cwd`、文件树和编辑状态
- 在合适的时机把结果回写到宿主系统

如果直接让 Agent 面对真实宿主文件系统，会立刻遇到几个问题：

1. 宿主业务模型和运行时文件系统语义混在一起，边界不清晰。
2. 多轮执行缺少稳定的工作区快照，难以复用上下文。
3. 每次命令都直读直写数据库，延迟高，而且难做缓存。
4. 写入失败、并发冲突、回放和审计都不好处理。

`iruka_vfs` 的设计目标，就是把这些能力沉淀成一个独立的运行时层。它不关心宿主业务里的 `Conversation`、`Project` 之类对象到底长什么样，只关心一件事：给 Agent 一个可控、可持久化、可缓存、可显式 flush 的虚拟工作区。

从代码结构上看，这个项目把能力边界收得很窄：

- 入口和对外 API 在 `iruka_vfs/workspace.py`
- 核心运行时在 `iruka_vfs/service.py`
- 命令解析和执行分别在 `iruka_vfs/command_parser.py`、`iruka_vfs/command_runtime.py`
- 运行时镜像和 checkpoint 机制在 `iruka_vfs/workspace_mirror.py`
- 热路径文件内容缓存位于 `iruka_vfs/memory_cache.py`

这意味着宿主系统只需要注入依赖，并把需要暴露给 agent 的内容映射成 `workspace_files`，就能把自己的业务对象挂接到这套 VFS 上。

## 二、整体定位：它不是 OS 文件系统，而是 Agent 的执行沙箱

`iruka_vfs` 提供的不是一个 POSIX 兼容文件系统，也不是 FUSE 挂载，而是一个面向 Agent 命令执行的“虚拟工作空间运行时”。

它的基本模型可以概括成：

```text
Host Models / Workspace Files
        |
        v
 RuntimeSeed + Dependency Injection
        |
        v
  Virtual Workspace Tree
        |
        +--> command parser
        +--> command runtime
        +--> workspace mirror
        +--> memory cache
        +--> checkpoint / flush
        |
        v
 DB / Redis
```

这里最关键的点有两个：

1. 它把宿主侧需要暴露给 agent 的文件统一映射进虚拟目录树，例如 `/workspace/files/...`、`/workspace/docs/...`。
2. 它把“命令执行态”从数据库里抽出来，优先落在运行时镜像和缓存里，再通过显式 `flush()` 或后台 checkpoint 回写。

这种设计非常适合以下场景：

- 章节编辑、文档改写、代码补丁等以“文件”为中心的 Agent 工作流
- 需要多轮复用工作区状态的交互式任务
- 需要可审计命令日志，但又不希望命令执行完全绑定数据库延迟的场景

## 三、对外 API 很轻，但背后的运行时并不轻

项目对外暴露的 API 非常克制，核心入口只有两个。

第一步，宿主系统配置依赖：

```python
from iruka_vfs import build_profile_dependencies, configure_vfs_dependencies

configure_vfs_dependencies(
    build_profile_dependencies(
        settings=...,
        runtime_profile="persistent",
    )
)
```

第二步，基于宿主工作区对象创建一个可复用句柄：

```python
from iruka_vfs import build_workspace_seed, create_workspace

workspace = create_workspace(
    workspace=workspace_row,
    tenant_id="demo",
    workspace_seed=build_workspace_seed(
        runtime_key="workspace:1",
        tenant_id="demo",
        workspace_files={
        "/workspace/files/document_1.md": load_text(),
        "/workspace/docs/brief.md": "# Brief\n\nSeeded from Python.\n",
        "/workspace/docs/todo.txt": "- inspect outline\n",
        "/workspace/docs/outline.md": outline_text,
        "/workspace/docs/index.md": skill_text,
        },
    ),
)
```

随后宿主侧通常围绕几类动作展开：

- `workspace.ensure(db)`：准备虚拟工作区
- `workspace.write_file(db, path, content)`：通过 Python 直接写虚拟文件
- `workspace.read_file(db, path)`：通过 Python 直接读单个虚拟文件
- `workspace.read_directory(db, path)`：批量读取某个目录下的文件内容
- `workspace.enter_agent_mode(db)` / `workspace.enter_host_mode(db)`：切换 workspace 控制权
- `workspace.bash(db, "...")`：执行一条虚拟 shell 命令
- `workspace.flush()`：在明确的持久化边界回写

这套接口轻量的原因，在于它把复杂度都藏到了 `RuntimeSeed` 和依赖注入下面。

### 3.1 RuntimeSeed：把宿主上下文投影到虚拟工作区

`RuntimeSeed` 定义在 `iruka_vfs/runtime_seed.py`，用于描述一次工作区初始化所需的最小信息：

- `runtime_key`：运行时身份
- `tenant_id`：租户隔离键
- `workspace_files`：初始化时批量注入到 `/workspace/...` 下的文件
- `metadata`：扩展元数据

这套模型非常实用，因为它没有要求宿主必须把所有业务对象都转换成数据库节点；只有真正需要暴露给 Agent 的内容，才会被投影进虚拟目录树。

## 四、核心流程：`ensure -> (python file api | bash) -> flush`

这个项目的主线并不复杂，但设计得很完整。按执行顺序看，最关键的是三个阶段。

## 五、`ensure`：把宿主状态构造成 Agent 可操作的虚拟目录树

`workspace.ensure(db)` 最终会进入 `iruka_vfs/service.py` 里的 `ensure_virtual_workspace()`。

这个过程大致做了以下几件事：

1. 校验 workspace 的租户归属。
2. 注册 `RuntimeSeed`，建立当前 workspace 的运行时上下文。
3. 创建根目录以及初始化需要的父目录，例如 `/workspace`、`/workspace/files`、`/workspace/docs`。
4. 把 `workspace_files` 同步到虚拟节点表。
5. 更新 workspace 元数据，例如虚拟主文件路径、可写根目录、上下文文件列表等。
6. 创建或获取 shell session。
7. 基于数据库中的所有节点构建一份 `WorkspaceMirror`。

从博客读者视角，最值得注意的是它创建的是稳定的虚拟目录树。也就是说，Agent 不需要知道宿主业务里“正文”“参考资料”“辅助文件”分别来自哪里，它只会看到一棵统一的树：

```text
/workspace
  /files
  /docs
  /runtime
```

这比把宿主概念直接暴露给模型更稳，因为模型面对的是统一文件语义，而不是业务对象语义。

## 六、workspace 现在有显式的 `host / agent` 访问模式

这次接口演进里，最重要的变化不是新增了几个方法，而是明确了 workspace 的控制权模型。

当前实现把 workspace 分成两种互斥模式：

- `host` 模式：允许宿主通过 Python API 直接读写文件
- `agent` 模式：允许 Agent 通过 `workspace.bash(...)` 操作工作区

默认情况下，`workspace.ensure(db)` 完成后 workspace 处于 `host` 模式。要把它交给 Agent，宿主需要显式调用：

```python
workspace.enter_agent_mode(db)
```

Agent 使用完成后，如果宿主还要直接读写文件，则需要切回：

```python
workspace.enter_host_mode(db)
```

这个改造的意义在于把“宿主侧文件操作”和“Agent 命令执行”变成阶段性互斥关系，而不是共享同一份可变运行时状态。这样更符合这套 runtime 当前的 workspace 级锁模型，也更容易定义 `flush()` 的边界。

## 七、除了 `bash`，宿主现在也可以直接通过 Python API 管理文件

这个项目最初更偏向“给 Agent 一个 shell 风格工作区”，但从宿主接入体验看，仅有 `workspace.bash(...)` 其实还不够顺手。很多场景下，宿主需要在不经过命令解释器的情况下直接做三件事：

- 初始化一批文件和目录
- 往某个虚拟路径直接写内容
- 批量读取某个目录下的文件内容

现在这套能力已经直接暴露在 workspace facade 上：

```python
workspace = create_workspace(
    workspace=workspace_row,
    tenant_id="demo",
    workspace_seed=build_workspace_seed(
        runtime_key="workspace:1",
        tenant_id="demo",
        workspace_files={
        "/workspace/files/document_1.md": "# Draft",
        "/workspace/docs/brief.md": "# Brief",
        "/workspace/docs/todo.txt": "- host seeded",
        },
    ),
)

workspace.ensure(db)
workspace.write_file(db, "/workspace/docs/generated.md", "hello")
brief = workspace.read_file(db, "/workspace/docs/brief.md")
docs = workspace.read_directory(db, "/workspace/docs")
workspace.enter_agent_mode(db)
```

这组 API 的设计有几个特点：

1. `workspace_files` 支持初始化时批量注入文件，父目录自动创建。
2. 相对路径会自动挂到 `/workspace` 下。
3. 路径不允许越出 `/workspace` 根目录。
4. `read_directory(...)` 返回 `{virtual_path: content}` 映射，适合宿主侧做批处理。

这样一来，宿主就有两条并行能力：

- 用 `workspace.bash(...)` 给 Agent 一个受控 shell 运行时
- 用 Python facade 直接做宿主侧文件读写

但这两条能力不是并行开放，而是通过 `host / agent` 模式切换来交接控制权。这比把所有文件动作都包装成命令字符串更实用，也更适合业务系统接入。

## 八、`bash`：不是调用系统 shell，而是执行一套受控命令运行时

`workspace.bash(db, raw_cmd)` 会进入 `run_virtual_bash()`，再走到 `iruka_vfs/command_runtime.py`。

这里有一个非常重要的设计选择：项目并没有把命令转发给真实 bash，而是实现了一套受控的命令解释器。

当前支持的命令包括：

- `pwd`
- `cd`
- `ls`
- `cat`
- `find`
- `rg` / `grep`
- `wc`
- `mkdir`
- `edit`
- `patch`
- `tree`
- `xargs`
- `echo`
- `touch`

它还支持基础 shell 语义：

- `&&`
- `;`
- `|`
- `>`
- `>>`
- `2>&1`

这意味着 Agent 获得的是一个“足够像 shell”的编辑环境，但所有读写都被限制在虚拟文件树内部。这样做有几个明显好处：

1. 可控。不会误碰宿主真实文件系统。
2. 可审计。每条命令都可以记录结构化结果。
3. 可扩展。新增命令只需要补 runtime 分发逻辑。
4. 可优化。命令执行天然可以对接 mirror 和 cache。

### 6.1 命令解析层足够小，但覆盖了 Agent 常用需求

`iruka_vfs/command_parser.py` 做的事情很朴素：

- `split_chain()` 负责解析 `&&` 和 `;`
- `parse_pipeline_and_redirect()` 负责解析管道和重定向
- `shell_tokens()` 用 `shlex` 做基础 tokenization
- `parse_options()` 解析 `--find`、`--replace` 这类长选项

它不是完整 shell parser，但它恰好覆盖了 Agent 文本编辑工作流里最常见的语法。这种克制是合理的，因为目标不是做一个 shell，而是做一个面向 Agent 的运行时。

### 6.2 `edit` 和 `patch` 才是这个系统的核心命令

从实现上看，`edit` 和 `patch` 是最关键的写入能力。

`edit` 的行为是：

- 定位目标文件
- 校验路径是否允许写入
- 读取文件当前内容
- 基于 `--find` / `--replace` 做一次或全量替换
- 调用 `_write_file()` 推进版本号并写入运行时态

`patch` 则支持两种方式：

- 简单的 `find/replace`
- 统一 diff 形式的 `--unified`

它内部还实现了一个轻量级 unified diff 应用器 `_apply_unified_patch()`，会检查上下文行、删除行和新增行是否匹配，并在失败时返回冲突信息。对 Agent 系统来说，这一点很关键，因为它能把“补丁失败”变成一个结构化反馈，而不是一段含糊的异常文本。

## 九、真正的性能关键：`WorkspaceMirror`

如果只把这套系统理解成“数据库里存一棵虚拟文件树”，那就低估了它。

这个项目最有价值的实现，其实是 `WorkspaceMirror`。

### 7.1 什么是 WorkspaceMirror

`WorkspaceMirror` 定义在 `iruka_vfs/models.py`，本质上是一份工作区在运行时内存中的完整镜像，包含：

- 当前 workspace、session 标识
- 所有节点的克隆副本 `nodes`
- 路径索引 `path_to_id`
- 父子关系索引 `children_by_parent`
- 当前 `cwd_node_id`
- workspace 元数据
- revision / checkpoint_revision
- dirty content / dirty structure / dirty session 标记
- 一把进程内 `RLock`

这个设计说明作者已经明确把“数据库状态”和“运行时热状态”区分开了。

数据库负责持久化和恢复，mirror 负责命令执行时的低延迟读写。

### 7.2 为什么要镜像而不是直接操作 ORM 实体

原因很直接：

1. ORM 对象适合事务提交，不适合高频命令态读写。
2. 路径解析、目录遍历、内容读取都是热点操作，直接打数据库成本高。
3. Agent 命令执行天然是“局部高频、周期 flush”的模式，适合先写内存态。
4. 需要把“文件树结构变更”和“内容变更”显式标脏，便于后续 checkpoint。

`build_workspace_mirror()` 的实现也很说明问题：它先把整棵工作区节点读出，再逐个 clone，然后重建路径和 children 索引。这基本就是把数据库里的工作区加载成一个可快速查询、可局部变更的内存模型。

## 十、写路径设计：先写 mirror，再 checkpoint / flush

`_write_file()` 很能体现这个系统的取舍。

如果当前 workspace 已经加载了 mirror，那么写入逻辑是：

1. 在 mirror 中找到目标节点
2. 直接修改 `content_text`
3. `version_no + 1`
4. 标记 `dirty_content_node_ids`
5. 推进 `revision`

也就是说，命令执行时的大部分写操作并不会立刻落库。

只有在以下情况才会走其他路径：

- 没有 mirror，且关闭了内存缓存：直接更新数据库节点
- 没有 mirror，但启用了 memory cache：先进入文件缓存，再由后台 worker 落库

这说明项目实际上准备了两层加速路径：

- 工作区级别的整体镜像
- 文件内容级别的局部内存缓存

前者更偏“完整运行时”，后者更偏“内容热缓存”。

## 十一、Checkpoint 与 `flush()`：把运行时脏状态安全落回持久层

这个项目没有把 `flush` 简化成“一次数据库提交”，而是实现了比较完整的 checkpoint 机制。

### 9.1 显式 `flush()` 是宿主的持久化边界

`workspace.flush()` 会调用 `flush_workspace()`，再进一步走到 `flush_workspace_mirror()`。

它的大体流程是：

1. 找到当前 workspace 的 mirror。
2. 获取 workspace 级别锁。
3. 从 mirror 中截取一批 dirty 状态快照。
4. 把脏节点、session cwd、workspace metadata 写回数据库。
5. 清理或重排 dirty 状态。
6. 如果仍然有脏数据，则重新入队 checkpoint。

这个接口的设计非常适合宿主系统在“回合结束”时调用。比如一轮 Agent 推理结束后，宿主可以把这次编辑视为一次清晰的 durability boundary，然后显式 `flush()`。

### 9.2 后台 worker + debounce + retry + dead letter

`iruka_vfs/workspace_mirror.py` 里真正有意思的是 checkpoint worker：

- 通过 Redis 队列调度待 flush 的 workspace
- 支持 debounce，避免高频编辑导致重复回写
- flush 失败后按指数退避重试
- 超过阈值进入 dead letter 集合
- 记录错误 payload 便于排查

这说明作者已经把它当成一个长期运行的 runtime，而不是 demo 级别脚本。对于 Agent 系统来说，这是很重要的成熟度信号，因为一旦开始支持多轮编辑、后台执行和异步回写，失败恢复就是必要能力，不是锦上添花。

## 十二、Memory Cache：另一条更细粒度的性能优化路径

除了 workspace mirror，项目还在 `iruka_vfs/memory_cache.py` 中实现了文件内容缓存。

它的几个关键点是：

- 按 `file_id` 缓存内容和版本号
- 维护 LRU
- 维护 dirty 集合
- 周期性后台 flush
- 通过 `version_no` 做乐观更新

`update_cache_after_write()` 会把写操作记成 pending versions，后台 `mem_cache_flush_worker()` 再批量尝试：

```sql
UPDATE virtual_file_nodes
SET content_text = ..., version_no = ...
WHERE id = :file_id AND version_no = :expected_version_no
```

这个条件更新说明它采用了轻量级乐观并发控制。如果 DB 中版本号已经变化，就说明有冲突，worker 会记录冲突指标而不是盲写覆盖。

虽然在有 workspace mirror 的路径上，memory cache 未必是主要热路径，但它让系统具备了另一种退化运行能力：即使没有完整 mirror，单文件读写也不必每次都直达数据库。

## 十三、数据模型设计：把“运行时对象”拆得足够清楚

从 demo 里的模型可以看出，系统最核心的持久化对象只有四类：

- `vfs_workspaces`
- `virtual_file_nodes`
- `virtual_shell_sessions`
- `virtual_shell_commands`

这种拆分很合理：

- workspace 表示虚拟工作区本身
- node 表示文件树节点
- session 表示 shell 上下文，例如 cwd
- command 表示命令日志和执行结果

也就是说，这个系统不是“给业务表加几个字段”，而是建立了一套完整的运行时数据模型。业务系统只需要保存自己的主对象，再把需要暴露给 Agent 的内容投影进这套模型里。

## 十四、依赖注入：让运行时和宿主解耦

`iruka_vfs/dependencies.py` 只有很少的代码，但地位很重要。

通过 `VFSDependencies`，宿主需要明确告诉运行时：

- 使用哪套 settings
- workspace / file node / shell command / shell session 对应哪些 ORM 模型
- 如何加载项目状态

这带来两个直接收益：

1. `iruka_vfs` 可以独立演进，不绑定具体业务模型。
2. 宿主可以替换底层 repository 或 ORM 实现，而不改命令运行时逻辑。

从工程实践上说，这是一个很正确的切分方式。Agent runtime 应该依赖抽象边界，而不是吞掉宿主业务上下文。

## 十五、一个最小示例如何串起整套机制

`examples/standalone_sqlite_demo.py` 是理解这个项目的最佳入口。

这个示例做了几件典型事情：

1. 用 SQLAlchemy 定义 demo 版 workspace、node、session、command 表。
2. 用 `InMemoryRedis` 模拟 Redis。
3. 创建一条宿主 workspace 记录。
4. 用 `WritableFileSource` 把一份业务文档文本挂载到 `/workspace/files/document_1.md`。
5. 调用：
   - `workspace.ensure(db)`
   - `workspace.write_file(db, ...)`
   - `workspace.enter_agent_mode(db)`
   - `workspace.bash(db, "cat ...")`
   - `workspace.bash(db, "edit ...")`
   - `workspace.enter_host_mode(db)`
   - `workspace.flush()`
6. 最后检查宿主文本是否被成功回写。

这个 demo 很能说明 `iruka_vfs` 的真正价值：它不是为了“模拟 shell”，而是为了把宿主文件变成一个可交互、可编辑、可持久化的 Agent 工作区。

## 十六、这个设计最值得借鉴的几个点

如果从系统设计角度总结，我认为这个项目最值得借鉴的是下面几点。

### 14.1 它先定义了运行时边界，再定义 API

很多 Agent 工程会先暴露一堆工具调用接口，再在业务里拼凑状态。`iruka_vfs` 反过来，先定义工作区、session、node、mirror、flush 这些运行时对象，再暴露 `ensure/bash/flush` 这样的简洁 API。

这种顺序更利于系统稳定演进。

### 14.2 它把“外部文件接入”收敛成 `read_text/write_text`

这是极其务实的抽象。宿主侧通常已经有自己的数据库、对象存储、权限系统，不需要 VFS 重新接管一遍。只要提供两个函数，就可以把宿主文件映射进虚拟空间。

### 14.3 它承认 Agent 工作流天然需要“延迟持久化”

命令执行频繁，flush 边界稀疏，这是 Agent 编辑系统的常态。mirror、memory cache、checkpoint worker 都是在服务这个事实，而不是强行把每次命令都做成同步事务。

### 14.4 它在一开始就考虑了失败恢复

checkpoint retry、dead letter、错误 payload、异步日志截断，这些都说明实现者没有把系统当成单机 demo，而是按真实服务组件在设计。

## 十七、当前实现的边界与可以继续演进的方向

当然，这套实现也有明确边界。

第一，它不是完整 shell，命令集是受限的。这是优点，也是约束。如果未来要支持更复杂的命令组合、glob、环境变量替换，解析层还需要继续扩展。

第二，它已经明确把 workspace 的控制权切成 `host / agent` 两种模式，并且 README 里也说明同一个 workspace 不应该并发执行 `workspace.bash(...)`。这说明当前并发模型是“单工作区串行执行 + 阶段性交接”优先，适合多数 Agent 场景，但如果以后要支持同一工作区多执行流协同，还需要更细粒度的并发控制。

第三，当前主写路径依赖运行时 mirror 与后台 checkpoint。这个设计很高效，但也要求宿主能够接受“显式 flush 才算 durable”这件事。如果宿主业务需要每个命令都强持久化，那么接入策略需要调整。

第四，路径权限控制目前主要围绕虚拟根目录和可写路径展开。若要面向更复杂的多角色、多文件权限体系，后续可以继续把 ACL 抽象出来。

## 十八、总结：`iruka_vfs` 本质上是在给 Agent 提供一个真正可运行的工作区

如果用一句话概括这个项目，我会这样描述：

`iruka_vfs` 不是一个简单的虚拟文件树，而是一个面向 Agent 编辑工作流的运行时内核。

它解决的不是“如何保存一棵目录树”这么简单的问题，而是下面这组更实际的工程问题：

- 如何把宿主文件安全地映射成 Agent 可操作空间
- 如何在多轮执行中维持稳定的工作区上下文
- 如何让命令执行既像 shell，又足够可控
- 如何把高频读写从数据库热路径中摘出来
- 如何在性能和持久化之间建立明确边界

从实现质量上看，这个仓库最有价值的部分不只是 API，而是背后的 runtime 组织方式：依赖注入、虚拟文件树、命令运行时、workspace mirror、memory cache、checkpoint worker，这些部件组合在一起，形成了一套相当完整的 Agent 文件工作区基础设施。

如果你的系统正在从“调用几个工具函数”走向“让 Agent 在一个持续存在的工作区里完成复杂编辑任务”，那么这类 runtime 设计是非常值得参考的。

---

## 附：可作为文中引用的源码位置

- `iruka_vfs/workspace.py`：对外工作区句柄与 `ensure`、模式切换、`bash`、`flush`
- `iruka_vfs/service.py`：核心服务入口、初始化、写入、flush
- `iruka_vfs/command_parser.py`：命令链、管道、重定向解析
- `iruka_vfs/command_runtime.py`：命令执行分发
- `iruka_vfs/workspace_mirror.py`：运行时镜像、checkpoint worker、flush
- `iruka_vfs/memory_cache.py`：文件内容缓存与后台刷盘
- `examples/standalone_sqlite_demo.py`：最小可运行示例
