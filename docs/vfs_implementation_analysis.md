# VFS 实现方案分析

## 1. 结论概览

当前仓库里的 VFS 不是映射到真实磁盘的文件系统，而是一个以数据库表为底座的虚拟工作区。核心实现当时集中在旧版的虚拟 shell service 中，通过 `run_virtual_bash(command)` 向 agent 暴露一组受控的“虚拟 shell”能力，把章节正文、项目上下文和临时笔记都组织成一棵虚拟目录树。

这套方案的设计重点不是做一个完整 shell，也不是做 POSIX 兼容文件系统，而是解决以下问题：

1. 给 agent 一个低摩擦的读写接口，让它可以像操作文件一样处理章节内容和上下文。
2. 把高频文本读写从“每次同步打数据库”改造成“以内存为热路径、数据库为持久层”。
3. 把命令日志记录和文件内容刷盘从主请求路径拆出来，尽量降低单次命令延迟。

从实现方式上看，它本质上是：

- 数据库存储虚拟目录和文件内容
- 进程内维护文件内容缓存和脏写缓冲
- 后台线程异步刷盘
- 后台线程异步写命令日志
- 通过有限命令集实现一个 agent 专用虚拟 shell

## 2. 代码位置和模块边界

核心相关模块当时包括：

- 虚拟 shell service
- SQLAlchemy 模型定义
- 数据库 session/base 定义
- PostgreSQL 搜索优化 SQL
- VFS 压测脚本
- API 路由入口
- 对话工作流集成层

## 3. 数据模型设计

### 3.1 `virtual_file_nodes`

定义见旧版 SQLAlchemy 模型。

这是 VFS 的核心表，用单表同时表示目录和文件：

- `workspace_id`：归属哪个 `AgentWorkspace`
- `parent_id`：父节点，自引用形成目录树
- `name`：当前层名字
- `node_type`：`dir` 或 `file`
- `content_text`：文件正文；目录节点一般为空字符串
- `version_no`：文件版本号
- `created_at` / `updated_at`

同时有唯一约束：

- `(workspace_id, parent_id, name)`

这保证了同一 workspace 的同一目录下文件名唯一，相当于数据库层实现了路径唯一性。

这一设计的优点是简单直接，路径树天然能用关系型结构表达。缺点是也很明显：

- 文件正文和元数据放在同一张表
- 大文本更新时是整字段覆盖，不是块级写入
- 搜索只能围绕 `content_text` 做优化

### 3.2 `virtual_shell_sessions`

定义见旧版 SQLAlchemy 模型。

用于维护 shell 会话状态，关键字段：

- `workspace_id`
- `cwd_node_id`
- `env_json`
- `status`

这使 VFS 具备“有状态 shell”的特征，至少可以保存当前工作目录。

### 3.3 `virtual_shell_commands`

定义见旧版 SQLAlchemy 模型。

保存命令执行日志：

- 原始命令 `raw_cmd`
- 解析后的结构 `parsed_json`
- `exit_code`
- `stdout_text`
- `stderr_text`
- `artifacts_json`
- `started_at` / `ended_at`

这张表主要用于调试、回放和观测，不是强审计链路，因为当前日志是支持异步丢弃的。

### 3.4 `virtual_patches`

定义见旧版 SQLAlchemy 模型。

用于记录 patch 应用过程：

- `workspace_id`
- `file_node_id`
- `base_version`
- `patch_text`
- `status`
- `conflict_json`
- `applied_version`

它不是单纯的文本替换记录，而是带版本基线和冲突信息的补丁轨迹。

## 4. 数据库连接与总体 IO 模式

数据库连接当时定义在同步 SQLAlchemy session/base 模块中：

- 默认使用同步 SQLAlchemy `Session`
- 默认数据库 URL 是 PostgreSQL，由宿主配置模块提供
- SQLite 只额外加了 `check_same_thread=False`

这里很关键：这套方案虽然用了“异步 IO”的思想，但不是 `async engine` 或 `AsyncSession` 的那套模型。它的异步方式是：

- Web 请求线程仍然使用同步 ORM
- 将慢路径工作放进后台线程
- 通过队列和共享内存结构实现异步日志写入、异步文件刷盘

所以更准确地说，它是“同步主流程 + 后台线程异步持久化”的模型。

## 5. VFS 目录结构和工作区初始化

工作区初始化逻辑在旧版虚拟 shell service 中完成。

每个 `AgentWorkspace` 会被初始化出固定目录结构：

- `/workspace`
- `/workspace/chapters`
- `/workspace/notes`
- `/workspace/context`

当前章节会映射为：

- `/workspace/chapters/chapter_{chapter.id}.md`

项目状态会被转成上下文文件，相关逻辑也在旧版虚拟 shell service 中完成：

- `outline.md`
- `characters.md`
- `world.md`
- `constraints.md`
- `notes.md`

这个设计有几个明显意图：

1. 当前章节文件是 agent 的主要编辑目标。
2. 项目背景被物化成普通只读文件，便于 `cat` 和 `rg`。
3. `/workspace/notes` 作为中间草稿和辅助文件的可写空间。
4. prompt 不是一次性把全部上下文塞给 agent，而是允许 agent 自己在 VFS 中检索。

## 6. workspace 快照缓存策略

除了文件缓存，代码还维护了一个 workspace 级轻量缓存，定义在旧版虚拟 shell service 中。

核心方法：

- `_get_cached_workspace_state()`
- `_set_cached_workspace_state()`

缓存内容主要包括：

- `workspace_id`
- `chapter_id`
- `session_id`
- `chapter_file`

它缓存的不是完整目录树，也不是文件内容，而是工作区初始化结果的关键元信息。主要目的是避免每次命令执行时都重新：

- 找根目录
- 建 `workspace/chapters/notes/context`
- 找当前 shell session
- 重建章节文件路径元数据

换句话说，这更像“工作区初始化快照缓存”，而不是完整 VFS 镜像缓存。

## 7. 命令执行模型

命令入口是 `run_virtual_bash()`。

执行流程如下：

1. 启动异步日志 worker（如未启动）。
2. 启动内存缓存刷盘 worker（如未启动）。
3. 确保虚拟工作区存在。
4. 获取当前 `VirtualShellSession`。
5. 执行命令链。
6. 提交本次同步事务。
7. 截断 stdout/stderr 用于日志持久化。
8. 将日志异步入队或同步写库。
9. 返回命令执行结果。

命令链处理也在旧版虚拟 shell service 中完成：

- 支持 `;`
- 支持 `&&`
- 顺序执行
- 前一段失败时，`&&` 后面的段跳过

单命令解析与执行见：

- `_run_single_command()`
- `_parse_pipeline_and_redirect()`
- `_shell_tokens()`

支持的命令集包括：

- `pwd`
- `cd`
- `ls`
- `cat`
- `rg` / `grep`
- `wc -l`
- `edit`
- `patch`
- `tree`
- `echo`
- `touch`

这说明作者并不是试图做一个全功能 shell，而是围绕“文本读取、搜索、替换、补丁、临时文件”这些 agent 高频操作做定制能力。

## 8. 每次同步 IO 的数据库策略

这一节拆开看读、写、路径解析、搜索和日志。

### 8.1 路径解析

路径解析通过 `_resolve_path()` 完成。

基本策略是逐级查库：

- 绝对路径从根节点开始
- 相对路径从 `cwd_node_id` 开始
- 每一级路径都查一次子节点

因此，像 `/workspace/chapters/chapter_1.md` 这种路径，如果没有额外缓存节点映射，就会发生多次小 SQL 查询。

这是当前方案一个明显特点：

- 文件内容有专门缓存
- workspace 初始化结果有轻量缓存
- 但路径到节点的映射没有额外的 path cache

所以目录深度和路径操作频率会直接影响 DB 往返次数。

### 8.2 读文件

统一通过 `_get_node_content()` 读取。

逻辑如下：

- 如果关闭内存缓存，直接返回 `node.content_text`
- 如果开启内存缓存，则在 `_mem_cache_entries` 中查找
- 缓存不存在时，从数据库节点内容加载到内存
- 后续读取直接命中缓存

因此下面这些命令都会吃到相同的内容缓存：

- `cat`
- `wc -l`
- `edit`
- `patch`
- `rg`

这把“数据库存文件内容”的模型转成了“数据库做持久化、进程内内存做热读”的模型。

### 8.3 写文件

写入口是 `_write_file()`。

分两种模式：

#### 缓存关闭

- 直接改 `node.content_text`
- `version_no + 1`
- `db.flush()`
- 本次命令请求同步把正文写入数据库

#### 缓存开启

- 调 `_update_cache_after_write()`
- 只更新内存 entry
- 内存中先增加版本号
- 把完整内容加入 `pending_versions`
- 标记为 dirty
- 当前请求不立即刷 `virtual_file_nodes.content_text`

这是整套方案最核心的性能优化。对高频文本改写来说，最重的成本通常不是命令解析，而是大段 `content_text` 的同步 UPDATE。这里通过 write-behind 缓存把这个成本移到了后台线程。

### 8.4 日志记录

命令日志生成发生在 `run_virtual_bash()` 的后半段。

策略是：

- 先执行命令并提交同步事务
- 再根据开关决定日志异步入队还是同步写库

如果异步日志开启：

- 通过 `_enqueue_virtual_command_log()` 入队
- 返回一个进程内生成的临时 `command_id`

如果关闭异步日志：

- 直接创建 `VirtualShellCommand`
- 同步提交

stdout/stderr 在入日志前还会通过 `_truncate_for_log()` 截断，避免单条日志体积过大。

## 9. 异步 IO 数据库策略

这里需要明确区分两条不同的异步链路：

- 异步命令日志
- 异步文件刷盘

虽然两者都用了后台线程，但语义和风险级别不同。

### 9.1 异步命令日志策略

相关 worker 在旧版虚拟 shell service 中启动，实际处理函数是 `_virtual_command_log_worker()`。

具体行为：

- 使用独立 `sessionmaker`
- 从 `_log_queue` 中取任务
- 单批最多聚合 100 条日志
- 一次性 `insert(VirtualShellCommand)`
- 成功后 `commit`
- 出错则回滚并丢弃本批

队列本身是有界队列：

- `_log_queue = queue.Queue(maxsize=5000)`

而且 `_enqueue_virtual_command_log()` 在队列满时直接丢日志：

- 不阻塞主线程
- 不重试
- 不降级为同步写库

这反映出明确的优先级排序：

- 主链路命令执行延迟优先
- 调试日志次之
- 日志允许在背压下丢失

所以这里的“异步 IO 数据库策略”本质是：

- 日志最终一致
- 允许丢失
- 批量落库
- 与主事务解耦

### 9.2 异步文件刷盘策略

相关 worker 在旧版虚拟 shell service 中启动，实际逻辑在 `_mem_cache_flush_worker()`。

策略细节如下：

#### 轮询机制

- 每隔 `MEMORY_CACHE_FLUSH_INTERVAL_SECONDS` 轮询一次
- 默认值为 `0.25` 秒

#### 批处理范围

- 每轮最多处理 `MEMORY_CACHE_FLUSH_BATCH` 个 dirty 文件
- 默认值为 `64`

#### 版本合并

对单个文件：

- 从 `pending_versions` 取全部待刷版本
- 只把最后一个版本作为最终落库版本
- 中间版本不逐条写 DB

这相当于将多次短时间写操作合并成一次最终 UPDATE，是典型的 write coalescing。

#### 乐观锁更新

刷盘 SQL 如下：

```sql
UPDATE virtual_file_nodes
SET content_text = :content_text,
    version_no = :new_version_no,
    updated_at = :updated_at
WHERE id = :file_id
  AND version_no = :expected_version_no
```

这里的 `expected_version_no` 来自 `entry.flushed_version_no`，也就是“缓存认为数据库当前已经持久化到哪个版本”。

#### 成功后处理

刷盘成功时：

- `flush_ok` 计数加一
- 更新 `flushed_version_no`
- 删除已落库的 pending 版本
- 如果没有剩余 pending，则把 dirty 状态清掉

#### 冲突处理

如果 `rowcount != 1`：

- 认为数据库版本和缓存预期不一致
- 计入 `flush_conflict`
- 清空 `pending_versions`
- 清除 dirty 标记

也就是说，冲突时当前实现不是“重试并合并”，而是“放弃本批待刷内容”。

#### 异常处理

如果 SQL 执行异常：

- 回滚事务
- 计入 `flush_error`

但异常后不会自动把本轮内容重新包装到独立可靠队列里，因此其恢复能力有限。

## 10. 文件缓存策略

缓存结构定义在旧版虚拟 shell service 中。

### 10.1 缓存条目结构

`FileCacheEntry` 包含：

- `file_id`
- `content`
- `version_no`
- `flushed_version_no`
- `pending_versions`
- `dirty`
- `size_bytes`
- `last_access_ts`

这说明缓存不仅承担“读缓存”作用，还承担“写缓冲区”作用。

### 10.2 全局缓存索引

进程内全局状态包括：

- `_mem_cache_entries`
- `_mem_cache_lru`
- `_mem_cache_dirty_ids`
- `_mem_cache_current_bytes`
- `_mem_cache_metrics`

这些结构由 `_mem_cache_lock` 统一保护。

### 10.3 缓存读取路径

缓存加载函数是 `_load_cache_entry_from_node_locked()`。

行为：

- 如果存在现有 entry，则命中，更新 LRU 顺序和访问时间
- 如果不存在，则从 `node.content_text` 构造新 entry
- 写入 `_mem_cache_entries`
- 增加 `_mem_cache_current_bytes`
- 触发 LRU 淘汰

### 10.4 写缓存路径

每次写时：

- 先找到或加载 entry
- 更新 `content`
- `version_no + 1`
- 把完整正文压入 `pending_versions`
- 标记 dirty
- 加入 `_mem_cache_dirty_ids`

这意味着缓存里的 `content` 始终是“最新视图”，而数据库里的 `content_text` 可能暂时滞后。

### 10.5 LRU 淘汰

淘汰逻辑在 `_evict_cache_if_needed_locked()`。

受两个阈值限制：

- 最大总字节数：`MEMORY_CACHE_MAX_BYTES`，默认 `32MB`
- 最大文件数：`MEMORY_CACHE_MAX_FILES`，默认 `300`

淘汰策略：

- 以 `_mem_cache_lru` 维护 LRU 顺序
- 淘汰最老的非 dirty entry
- 如果最老 entry 是 dirty，则直接停止淘汰

这一点非常关键。因为 dirty entry 不可淘汰，所以当前缓存不是一个“纯最佳努力 read cache”，而是一个“read cache + write-back buffer”的混合结构。

优点：

- 未刷盘数据不会因为内存回收而丢失

代价：

- 如果脏条目太多，缓存回收会受阻
- 极端情况下可能导致缓存占用持续偏高

### 10.6 监控指标

缓存指标通过 `snapshot_virtual_fs_cache_metrics()` 暴露。

包括：

- `cache_hit`
- `cache_miss`
- `write_ops`
- `flush_ok`
- `flush_conflict`
- `flush_error`
- `evicted`
- `entries`
- `dirty_entries`
- `cache_bytes`

这对定位缓存收益和异步刷盘稳定性非常有帮助。

## 11. 搜索路径与数据库优化策略

搜索相关逻辑位于：

- `_search_nodes()`
- `_collect_files_for_search()`

### 11.1 非 PostgreSQL 情况

如果当前数据库不是 PostgreSQL：

- 目标如果是文件，直接返回该文件
- 目标如果是目录，调用 `_collect_files()` 深度遍历目录树
- 遍历过程会多次 `_list_children()` 查库
- 拿到文件后在 Python 里逐行匹配

这意味着 SQLite 下目录级搜索会有明显更多的数据库往返。

### 11.2 PostgreSQL 情况

作者专门做了 PG 优化：

1. 使用递归 CTE 一次性拉出整棵子树中的文件。
2. 对字面量 pattern，在 SQL 里先做 `LIKE` 或 `ILIKE` 候选过滤。
3. 再在 Python 里逐行产生最终 `path:line:text` 结果。

这样做的收益有两个：

- 目录递归遍历从多次 round trip 变成一次 SQL round trip
- 利用数据库先筛候选文件，减少 Python 逐行扫描量

这里作者没有把最终“行号匹配”完全下推到 SQL，是合理的，因为 shell 风格输出更适合由 Python 拼接，且 regex 语义也未必完全等价。

### 11.3 PostgreSQL 索引策略

索引脚本见仓库中的 `sql/virtual_fs_pg_search_indexes.sql`。

包括：

- `pg_trgm` 扩展
- `(workspace_id, parent_id)` 索引
- `(workspace_id, name)` 索引
- `content_text gin_trgm_ops` GIN 索引

各自意义：

#### `(workspace_id, parent_id)`

用于：

- 列目录
- 递归子节点查找
- 路径遍历热点

#### `(workspace_id, name)`

用于：

- 同 workspace 下按名字查子节点

虽然严格说路径解析更像 `(workspace_id, parent_id, name)` 组合访问，但当前至少已经把父子关系和名称两类热点拆开加速。

#### `content_text gin_trgm_ops`

用于：

- `LIKE`
- `ILIKE`
- 字面量子串匹配预过滤

尤其适合 `rg` 的 literal 模式候选缩小。

## 12. 命令级行为与数据库访问侧重点

### 12.1 `cat`

执行入口在旧版虚拟 shell service 的 `cat` 分支中。

数据库策略：

- 先做路径解析
- 找到节点后读内容
- 开启缓存时，正文通常不再访问数据库正文列

优化重点：

- 减少正文重复读取
- 但路径解析仍需查库

### 12.2 `edit`

执行入口在旧版虚拟 shell service 的 `edit` 分支中。

流程：

- 路径解析
- 权限检查
- 读取全文
- 在 Python 中做字符串替换
- 调 `_write_file()`

开启缓存时：

- 主线程只改内存，不同步刷 `content_text`

所以 `edit` 是缓存收益最明显的命令之一。

### 12.3 `patch`

执行入口在旧版虚拟 shell service 的 `patch` 分支中。

流程：

- 路径解析
- 权限检查
- 读取全文
- 在 Python 内应用 unified patch 或简单替换
- 先写一条 `VirtualPatch`
- 再 `_write_file()`

这里的有趣点是：

- patch 元数据是同步记录的
- 文件正文更新在缓存开启时是异步刷盘的

因此 patch 轨迹可能比正文持久化更早落库。

### 12.4 `echo >>` / `>`

重定向逻辑也在旧版虚拟 shell service 中实现。

流程：

- 先执行前半段 pipeline
- 再解析目标路径
- 校验写权限
- 如目标文件不存在则创建
- `>>` 时先取旧内容，再拼接新内容
- 调 `_write_file()`

对 append 类写法来说，这套缓存尤其有效，因为 DB 不再每次同步更新整份正文。

## 13. 权限和可写范围控制

权限控制函数是 `_allow_write_path()`。

规则如下：

- `/workspace/notes/**` 允许写
- 当前章节文件允许写
- `/workspace/chapters` 下其他章节文件不允许写
- `/workspace/context/**` 不允许写
- 其他路径默认只读

这是个非常重要的安全边界。它说明 VFS 不只是“文件接口”，还是 agent 的工作沙箱。

这种限制既符合业务目标，也减少了 agent 误操作空间：

- 能改当前章
- 能做中间笔记
- 不能改其他章节
- 不能篡改项目背景上下文

## 14. 一致性模型和风险

这是当前方案最需要谨慎理解的一部分。

### 14.1 进程内读写一致性

在单进程内，写完后再次读取通常会命中同一个缓存 entry，因此：

- 刚写马上读是可见的
- agent 视角下读后写体验接近强一致

### 14.2 数据库持久化一致性

对数据库本身来说，开启缓存后文件正文是延迟写入的，因此：

- DB 中的 `content_text` 可能短时间落后于进程内缓存
- 是最终一致，不是立即一致

### 14.3 崩溃恢复风险

因为 dirty 内容主要存在于进程内内存，如果进程在刷盘前崩溃：

- 最近一批尚未 flush 的变更会丢失

当前实现没有：

- 落地型 write-ahead queue
- 本地 WAL
- 崩溃恢复重放机制

所以这是一套“性能优先、持久性适度妥协”的方案。

### 14.4 多进程一致性风险

缓存是进程内全局 dict，而不是共享缓存。如果 FastAPI 以多 worker 运行：

- 每个 worker 都有自己的缓存
- 每个 worker 都有自己的异步刷盘线程
- worker 之间不共享最新内存内容
- 只能靠数据库 `version_no` 做乐观锁冲突检测

这会带来两个问题：

1. 一个 worker 刚写完但尚未刷盘时，另一个 worker 读不到它的最新内容。
2. 如果不同 worker 修改同一文件，刷盘冲突可能导致其中一方缓存写入被丢弃。

### 14.5 冲突处理的局限

当前冲突处理逻辑相对粗糙：

- 一旦发现 DB `version_no` 与 `expected_version_no` 不一致
- 就清空该文件的 `pending_versions`
- 取消 dirty 状态

这意味着当前实现没有做：

- 基于新版本重放 pending 修改
- 自动三方合并
- 冲突后重试

所以它更像“冲突检测 + 放弃本次缓存写回”，而不是“冲突解决”。

### 14.6 命令日志不是强审计

由于命令日志采用异步批量写入，且队列满时直接丢弃：

- 命令成功并不代表日志一定持久化
- 返回的 `command_id` 可能只是进程内临时 ID

因此 `virtual_shell_commands` 更适合调试和观测，不适合作为强审计数据源。

## 15. 压测脚本体现的优化目标

压测由独立 benchmark 脚本完成。

它做了 `cache off` 和 `cache on` 的对比，覆盖以下操作：

- 多次 `cat`
- 多次 `echo >>`
- 多次 `edit`
- 一次删除文本
- 多次 `rg`

统计指标包括：

- 总耗时
- 吞吐量
- 缓存命中与刷盘指标
- `consistency_error_rate`

其中 `consistency_error_rate` 被定义为：

- `(flush_conflict + flush_error) / flush_total`

这非常能说明作者关注的核心问题：

- 并不是“命令执行功能对不对”这么简单
- 而是“缓存加速后，异步刷盘阶段的一致性风险有多大”

这也从侧面证明，当前 VFS 的主要性能瓶颈确实被认为在“正文同步写 DB”这一层。

## 16. 方案优点总结

### 16.1 非常契合 agent 任务形态

agent 处理章节编辑时，本质上高频做的是：

- 读文件
- 搜索上下文
- 局部替换
- 生成 patch
- 写临时笔记

当前命令集正好覆盖这一组高频动作，没有引入大量无关 shell 能力。

### 16.2 存储结构简单

单表目录树 + 会话表 + 命令日志表 + patch 表，结构清晰，维护成本低。

### 16.3 热路径加速明确

最重的正文同步 UPDATE 被移出请求主路径，这对大文本编辑非常有效。

### 16.4 搜索优化有针对性

PostgreSQL 下的递归 CTE + trigram 索引组合是有实际效果的，不是停留在概念上。

### 16.5 安全边界清楚

可写范围严格受控，适合 agent 沙箱。

## 17. 方案短板总结

### 17.1 不是强持久一致系统

缓存未刷盘前进程崩溃会丢最新写入。

### 17.2 不是天然多进程安全

进程内缓存无法跨 worker 共享，多实例部署下一致性会明显变差。

### 17.3 路径解析仍偏依赖数据库小查询

虽然正文缓存了，但路径节点本身没有专门缓存，深层路径操作仍会频繁查库。

### 17.4 冲突处理能力有限

当前只有检测，没有真正恢复或合并。

### 17.5 日志链路可丢

异步日志是典型 best-effort，不适合作为可靠审计来源。

## 18. 一句话总结

这套 VFS 本质上是一个“以 PostgreSQL 为持久层、以进程内 LRU 写回缓存为加速层、以受限 shell 命令为交互层”的 agent 专用虚拟文件系统。它的优化重点是降低高频文本 IO 对数据库同步写的压力，而不是提供强一致、可恢复、跨进程共享缓存的完整文件系统语义。

## 19. 后续可继续补充的方向

如果后续还要继续深化分析，建议从下面几个方向继续展开：

1. 逐命令统计数据库往返次数，分别对比 PostgreSQL / SQLite、缓存开启 / 关闭。
2. 评估多 worker 部署时的实际一致性问题和冲突概率。
3. 设计“本地文件落盘版”替代方案，分析与当前 DB 版在延迟、审计、恢复能力上的差异。
4. 评估是否需要引入路径节点缓存、共享缓存或持久化写队列。

新增一个可执行的 agent 视角等待时间报告脚本：

- agent wait benchmark script

这个脚本的衡量口径是：

- 一次 `run_virtual_bash()` 调用 = 一次 agent 可感知等待事件
- 只统计这次调用的墙钟时间
- 异步 flush / drain 时间单独列出，不混入 agent wait percentile

典型用法：

```bash
python run_vfs_agent_wait_report.py \
  --mode compare \
  --db-url 'postgresql+psycopg://postgres:postgres@127.0.0.1:5432/iruka_agent' \
  --input /Users/tc/Downloads/test.txt \
  --markdown-out ./vfs_agent_wait_report.md
```

报告会输出：

- 整体 agent wait 的 mean / P50 / P95 / P99 / max
- 按命令类型拆分的等待时间统计
- 最慢样本
- cache flush 背景指标
- `cache off` 与 `cache on` 的 agent 视角对比

## 20. 命令级内存路径与 PostgreSQL 路径对照表

下面这张表从“单次命令执行”的角度拆开，说明哪些步骤主要走内存，哪些步骤仍然会直接或间接命中 PostgreSQL。

| 命令 | 主要内存路径 | 必然或高概率走 PG 的路径 | 说明 |
| --- | --- | --- | --- |
| `cat file` | 文件正文命中 `_mem_cache_entries` 后可直接返回缓存内容 | 路径解析 `_resolve_path()`；`cwd` 节点读取；命令结束后的事务提交；命令日志落库 | 正文可能不查 PG，但“先找到这个文件节点”通常仍要查 PG |
| `cat file1 file2` | 每个文件正文都可能命中内存缓存 | 每个参数都要各自做路径解析；日志落库 | 文件越多，路径查询越多 |
| `edit file --find --replace` | 旧正文读取可命中缓存；替换逻辑完全在 Python 内存中；新正文先写入缓存 | 路径解析；权限检查时读取 workspace metadata；异步 flush worker 最终 `UPDATE virtual_file_nodes`；日志落库 | 主线程写正文不直接打 PG，但后台一定会刷 PG |
| `patch --path file ...` | 旧正文读取可命中缓存；patch 应用在内存中；新正文先写缓存 | 路径解析；同步插入 `virtual_patches`；异步 flush worker 刷正文；日志落库 | patch 元数据是同步写 PG 的 |
| `echo xxx > file` | `echo` 输出在内存；若目标文件正文已缓存，覆盖时只改缓存 | 路径解析；必要时创建节点；权限检查；异步 flush worker 刷正文；日志落库 | 重定向不是纯内存动作，因为目标节点和权限都要查 |
| `echo xxx >> file` | 旧正文可从缓存读取；拼接和新正文生成在内存；写入缓存 | 路径解析；必要时创建节点；异步 flush worker 刷正文；日志落库 | append 的正文路径受缓存收益很大，但 PG 仍参与控制流 |
| `rg pattern file` | 文件正文可能命中缓存；逐行匹配在 Python 内存中 | 路径解析；如果是 PG 模式，`_collect_files_for_search()` 会先执行 SQL 或递归 CTE 取候选文件；日志落库 | 即使正文缓存了，PG 下搜索仍常先做候选筛选 |
| `rg pattern dir` | 候选文件逐行扫描在内存中 | 路径解析；PG 下递归 CTE 拉子树；非 PG 下递归 `_list_children()` 多次查库；日志落库 | 目录搜索是最明显受 PG 或 DB IO 影响的命令之一 |
| `wc -l file` | 正文可命中缓存；行数统计纯内存 | 路径解析；日志落库 | 比 `cat` 更轻，但仍不是零 PG |
| `ls dir` | 几乎没有正文缓存收益 | 路径解析；`_list_children()` 查子节点；日志落库 | 本质是目录元数据查询，主要看 PG |
| `tree` | 纯渲染字符串在内存 | 递归 `_list_children()`，每层目录都查 PG；日志落库 | `tree` 基本是元数据遍历，强依赖 DB |
| `cd dir` | 无明显内容缓存收益 | 路径解析；更新 `VirtualShellSession.cwd_node_id`；事务提交；日志落库 | `cd` 是纯控制面操作，主要走 PG |
| `touch file` | 若文件已存在，仅少量内存参与 | 路径解析；权限检查；可能创建 `virtual_file_nodes`；更新 `updated_at`；日志落库 | `touch` 主要是元数据写操作 |
| `pwd` | 字符串拼装在内存 | 可能读取当前 `cwd` 节点及其父链；日志落库 | 比较轻，但也不是完全脱离 PG |

再抽象一层，可以把 VFS 命令链分成下面几类：

| 层 | 是否主要受 PG 影响 | 原因 |
| --- | --- | --- |
| 文件正文读取 | 中等 | 命中缓存后弱依赖 PG，但首次加载和路径解析仍要查 |
| 文件正文写入 | 高 | 主线程可先写缓存，但后台 flush 一定写 PG |
| 路径解析 | 高 | 当前没有 path cache，逐层节点查找基本都打 PG |
| 目录遍历 | 高 | `ls`、`tree`、目录级 `rg` 都依赖子节点查询或递归 CTE |
| 搜索候选筛选 | 高 | PG 模式下故意让数据库先做 subtree 和 `LIKE`/`ILIKE` |
| patch 与日志元数据 | 高 | 这些是同步或后台最终写 PG 的 |
| 会话状态 | 高 | `cwd`、session 恢复、状态更新都在 PG |

所以更准确的描述不是“agent 直接操作纯内存文件系统”，而是：

- 文件正文热数据很多时候在内存里读写
- 但路径、目录、session、日志、搜索候选筛选和最终持久化仍然明显依赖 PostgreSQL
- 压测测的是完整 VFS 命令链，而不是单独测一个缓存中的字符串对象
