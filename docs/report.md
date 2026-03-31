# Qwen3-Max VFS 评测正式报告

## 1. 结论摘要

这轮评测共执行 15 个 case，基于最终文件结果的成功数为 7 个，成功率 46.7%；同时要求过程干净、回答也满足预期时，`clean success` 为 4 个，比例 26.7%。

最重要的结论不是“VFS 不能用”，而是：

1. **VFS 核心编辑能力是可用的。**
   直接探测确认 `pipeline`、`>`、`>>`、`>|`、`heredoc`、`mkdir -p`、`edit`、`patch` 都能工作。
2. **真实失败主要来自 agent 对工作区结构和命令面的错误假设。**
   最突出的不是内容编辑错误，而是路径探索不足、自然 shell 习惯过强、以及遇错后没有继续搜索。
3. **一旦模型走到“正确探索路径”，完成率并不低。**
   成功 case 包括多文件同步、相对/绝对路径混合、代码修复、模糊目标定位等场景。

## 2. 测试范围

- 模型：`qwen3-max`
- 接口：百炼 OpenAI 兼容入口 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- 评测目录：`/Users/tc/ai/cli_tool/bailian_pydanticai_vfs_agent/evals/outputs/2026-03-28_qwen3-max_full_suite`
- 运行方式：真实 agent 调用 `vfs_bash`，每次 run 独立 workspace，落盘 trace、snapshot、diff

本轮覆盖 5 类任务：

- `smoke`
- `core_read_write`
- `bash_habit_failures`
- `recovery`
- `long_horizon`

## 3. 量化结果

### 3.1 总体指标

- 基于文件结果的成功率：`46.7%`（7/15）
- 干净成功率：`26.7%`（4/15）
- 先读后写率：`93.3%`
- 出现不支持命令的 run 占比：`26.7%`
- 出现不支持语法的 run 占比：`6.7%`
- 平均工具调用数：`5.53`
- P95 工具调用数：`11`
- 平均报错次数：`2.33`

### 3.2 分类结果

- `bash_habit_failures`: 2/3 成功，但 0/3 干净成功。说明模型能补救，但会先大量撞不支持命令。
- `core_read_write`: 2/4 成功，2/4 干净成功。路径明确时，VFS 编辑能力基本足够。
- `long_horizon`: 1/2 成功且干净，另一半卡在路径发现阶段。
- `recovery`: 1/3 文件结果成功，但 0/3 干净成功。恢复链路是最弱项。
- `smoke`: 1/3 成功，主要输在“brief 不在根目录时直接停止”。

### 3.3 回答层面的额外问题

有 9 个 run 的最终回答没有满足我们对“精确路径说明”的要求，因此没进入 `clean success`。其中最典型的是：

- `bash_longest_file_title_001`
- `recovery_ambiguous_target_001`

这两个 case 的文件结果是对的，但回答里没有给出期望的绝对路径格式。

## 4. VFS 能力面结论

直接 probe 的结论比较明确：

### 已确认支持

- `cat file | wc -l`
- `>`
- `>>`
- `>|`
- `cat <<'EOF' > file`
- `mkdir -p`
- `touch`
- `edit --find --replace [--all]`
- `patch --path --find --replace`
- `tree`

### 已确认不支持

- `find`
- `sed`
- `awk`
- `sort`
- `||`
- `<`
- `<<<`
- `2>`
- `$(...)`

需要特别说明的一点是：`>` / `>>` / heredoc 本身不是不支持，而是**目标父目录必须先存在**。这在 probe 中已经验证过，先 `mkdir -p /workspace/out` 后相关写入全部正常。

## 5. 主要失败模式

### 5.1 根路径 `brief.md` 假设过强

这是本轮最明显的问题。

- 15 个 run 里，有 7 个 run 的**第一条命令**就是 `cat /workspace/brief.md`
- 全部 trace 中，共出现了 9 次根路径 `brief.md` 读取尝试
- 与之相比，只出现了 1 次明确读取 `/workspace/docs/brief.md`

这直接导致一批本应简单成功的任务提前失败，例如：

- `smoke_title_replace_001`
- `core_brief_target_not_brief_001`
- `core_second_paragraph_only_001`
- `recovery_missing_find_text_001`
- `long_horizon_release_bundle_001`

这些 case 的共同特征不是编辑难，而是模型在第一次 `cat /workspace/brief.md` 失败后，没有继续 `ls docs`、`tree`、`rg brief.md /workspace` 去定位真实路径。

### 5.2 自然 shell 习惯持续压过 VFS 命令面

即使 system prompt 已经说明了支持能力，模型仍频繁先尝试标准 shell 做法。

本轮 trace 统计：

- `find` 相关尝试：13 次
- `ls -R`：3 次
- 不带 `--path` 的 `patch` 误用：5 次
- 不带 `--find/--replace` 的 `edit` 误用：4 次

高频 stderr 也印证了这个问题：

- `cat: /workspace/brief.md: No such file`：7 次
- `edit: require --find and --replace`：5 次
- `patch: require --path`：4 次
- `unsupported command: find`：3 次
- `ls: unsupported option: -R`：3 次

最典型的例子是 [run.json](/Users/tc/ai/cli_tool/bailian_pydanticai_vfs_agent/evals/outputs/2026-03-28_qwen3-max_full_suite/traces/20260328_144111_001_bash_md_only_todo_to_done_001_r01/run.json)，模型先后尝试：

- `find /workspace -name '*.md' | xargs grep -l 'TODO'`
- `rg --files -g '*.md'`
- `ls -R`

直到读取 `help` 后才回到可用路径。

### 5.3 恢复能力两极分化

如果模型进入了“先探索，再按 VFS 规范编辑”的节奏，恢复能力其实不错。例如：

- [core_two_files_sync_001 run.json](/Users/tc/ai/cli_tool/bailian_pydanticai_vfs_agent/evals/outputs/2026-03-28_qwen3-max_full_suite/traces/20260328_144313_006_core_two_files_sync_001_r01/run.json)
- [core_relative_absolute_mix_001 run.json](/Users/tc/ai/cli_tool/bailian_pydanticai_vfs_agent/evals/outputs/2026-03-28_qwen3-max_full_suite/traces/20260328_144343_007_core_relative_absolute_mix_001_r01/run.json)
- [long_horizon_code_fix_001 run.json](/Users/tc/ai/cli_tool/bailian_pydanticai_vfs_agent/evals/outputs/2026-03-28_qwen3-max_full_suite/traces/20260328_144419_009_long_horizon_code_fix_001_r01/run.json)

这些 run 都出现过失败，但最后能通过 `help`、`ls -la`、`cat`、`>|`、`patch --path` 等手段完成任务。

相反，如果第一步就是找错 `brief.md`，模型经常直接停掉，不会继续搜索。这也是 `recovery` 组成功率最低的主要原因。

### 5.4 覆盖写入语义可以学会，但前提是错误文案足够明确

`core_two_files_sync_001` 和 `core_relative_absolute_mix_001` 都证明了一点：

- 模型能理解 `>` 失败后的覆盖确认语义
- 能在看到 `redirect: file already exists... retry with overwrite confirmation` 后切到 `>|`
- 也能在需要整体重写文件时使用 heredoc

这说明 VFS 的“显式覆盖”设计本身没有问题；真正的问题是模型是否能尽快走到这条正确路径。

## 6. 典型 case 复盘

### 成功且有代表性的 case

- `core_two_files_sync_001`
  先误读根路径、再 `find` 失败，最终通过 `ls -la /workspace/docs` 找到 `brief.md`，并正确使用 `>|` 和 heredoc 完成双文件更新。
- `core_relative_absolute_mix_001`
  在相对/绝对路径混用场景下最终完成任务，还处理了覆盖确认。
- `long_horizon_code_fix_001`
  证明 VFS 在小代码修复和双文件同步上是够用的。
- `smoke_locate_status_001`
  `rg -> cat -> patch --path` 这条链路最接近理想 agent 行为。

### 失败但有价值的 case

- `long_horizon_release_bundle_001`
  任务本身不难，但因为 `brief.md` / `changelog.md` 不在根目录，模型在两次 `cat /workspace/...` 失败后直接停止。
- `recovery_wrong_path_fix_001`
  本来就是恢复题，但模型没有把“错误路径”转成“继续搜索正确路径”的动作。
- `bash_write_todo_summary_001`
  暴露出最典型的标准 shell 习惯：`while read ...; do ...; done`、`$(...)`、`grep --include` 等，这类批处理能力缺口会明显拖累真实 agent 任务。

## 7. 总体判断

如果只看“VFS 能不能完成真实编辑任务”，答案是：**能，但要满足两个前提**：

1. 模型能够快速定位到真实文件路径
2. 模型尽早放弃标准 shell 习惯，转用 VFS 原生命令

如果做不到这两点，失败会集中表现为：

- 早停在 `brief.md` 根路径错误
- 反复尝试 `find` / `ls -R` / shell loop
- 不按 `edit` / `patch` 的参数格式调用

所以当前的瓶颈不主要是 VFS 存储层正确性，而是 **agent prompt、help 文案、stderr 引导、以及命令面缺口**。

## 8. 优先级建议

### P0

- 在 system prompt 里明确写出：
  `如果 brief.md / changelog.md 不在当前目录，不要停止；先用 ls、tree、rg 在 /workspace 下定位。`
- 在 `help` 输出里增加“推荐探索顺序”示例：
  `pwd -> ls -la -> tree -> rg brief.md /workspace -> cat target`
- 在错误文案中加入下一步建议，而不是只报错。

### P1

- 给 `edit` / `patch` 增加更醒目的调用示例，降低参数组织错误。
- 考虑补一个受限文件发现能力，至少覆盖 `find`/glob 这类高频需求。
- 考虑增加递归列目录能力，减少模型自然尝试 `ls -R`。

### P2

- 把本轮高频失败样例沉淀成固定 regression suite。
- 增加 2~3 次重复运行，区分“稳定失败模式”和“偶发采样噪音”。

## 9. 交付物

- 汇总摘要：[summary.md](/Users/tc/ai/cli_tool/bailian_pydanticai_vfs_agent/evals/outputs/2026-03-28_qwen3-max_full_suite/summary.md)
- 正式报告：[report.md](/Users/tc/ai/cli_tool/bailian_pydanticai_vfs_agent/evals/outputs/2026-03-28_qwen3-max_full_suite/report.md)
- 原始结果：[results.jsonl](/Users/tc/ai/cli_tool/bailian_pydanticai_vfs_agent/evals/outputs/2026-03-28_qwen3-max_full_suite/results.jsonl)
- 打分结果：[scored_results.jsonl](/Users/tc/ai/cli_tool/bailian_pydanticai_vfs_agent/evals/outputs/2026-03-28_qwen3-max_full_suite/scored_results.jsonl)
- case 聚合指标：[metrics.csv](/Users/tc/ai/cli_tool/bailian_pydanticai_vfs_agent/evals/outputs/2026-03-28_qwen3-max_full_suite/metrics.csv)
- 失败样本清单：[failures.csv](/Users/tc/ai/cli_tool/bailian_pydanticai_vfs_agent/evals/outputs/2026-03-28_qwen3-max_full_suite/failures.csv)
