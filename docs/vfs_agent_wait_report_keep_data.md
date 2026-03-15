# VFS Agent Wait Report

## Scope

- Mode: `on`
- Input: `/Users/tc/Downloads/test.txt`
- Input size: 47870 chars / 1194 lines
- Warmup loops excluded from summary: 2
- Drain excluded from agent wait: 2.033 s
- Total wall time including drain: 8.281 s

## Agent-Visible Wait Summary

| Metric | Value |
| --- | --- |
| count | 37 |
| mean | 66.24 ms |
| min | 56.05 ms |
| p50 | 60.81 ms |
| p95 | 82.85 ms |
| p99 | 145.93 ms |
| max | 165.47 ms |
| stdev | 18.91 ms |

## Agent-Visible Wait By Operation

| Operation | Count | Mean | P50 | P95 | P99 | Max |
| --- | --- | --- | --- | --- | --- | --- |
| `append_echo` | 12 | 61.87 ms | 60.61 ms | 68.19 ms | 69.71 ms | 70.09 ms |
| `edit_delete_span` | 1 | 165.47 ms | 165.47 ms | 165.47 ms | 165.47 ms | 165.47 ms |
| `edit_replace_to_dst` | 4 | 62.83 ms | 59.68 ms | 71.87 ms | 73.51 ms | 73.92 ms |
| `edit_replace_to_src` | 4 | 58.67 ms | 58.24 ms | 60.64 ms | 60.91 ms | 60.98 ms |
| `read_cat` | 8 | 69.54 ms | 63.81 ms | 97.42 ms | 108.43 ms | 111.18 ms |
| `search_rg` | 8 | 62.60 ms | 61.06 ms | 71.78 ms | 74.97 ms | 75.77 ms |

## Slowest Agent Wait Samples

| Operation | Duration | Command |
| --- | --- | --- |
| `edit_delete_span` | 165.47 ms | `edit /workspace/chapters/chapter_29.md --find '波马特人反应实在太慢，社交启动时间过长，现在他才说出第一句话——
 
　　“新奇，你是在拍船长的马屁吗？” 
 
　　新奇飞快地扭过前置摄像头（快速闪红灯），速度快得不像他这个老旧停产型号该有的性能。
 
　　“你这个愚蠢的毛怪！给我闭嘴！你们种族几万年来都不能进化出胼胝体就是因为不该说的废话太多堵住了你们的白质带！” 
 
　　一阵尴尬的沉默。
 
　　卡特琳娜揉了揉眉心。她那漂亮的鼻梁上有一道浅浅的伤疤。
 
　　她缓缓开口：“新奇，公共硬盘里的《莫瑞秀》也是你下载的吧？”
 
　　新奇回头朝向卡特琳娜（稳定的蓝灯）：“完全正确。那是一个非常好的有机类智慧生命极端情绪展示的样本集。” 
 
　　“你可真是善于学习。”
 
　　新奇又进行了深度思考（蓝灯偶尔转黄一下）：“这是讽刺对吗？”
 
　　卡特琳娜摇着头笑笑转身走开，准备继续涂另一只手的指甲。她不喜欢应付人类，还好船上除了她自己没有人类成员。
 
　　卡特琳娜转身离开后，奇普挪了挪毛绒绒的身体，靠近新奇小声说道：“新奇，你刚才那些……能换成短点的句子再说一次吗？我没听懂。”
 
 
第二章
 
　　殷城星冒险家协会的大厅里三五成群地坐着各种打扮的人，都是来寻找委托的海盗、走私者或者雇佣兵，又或者三者都是。
 
　　突然，人群喧嚣起来。
 
　　“是那个最近上热门的‘红发的幽影’！”
 
　　“哇靠，真是她！骑飞船的巫婆！”
 
　　“据说这姐们儿走到哪儿睡到哪儿' --replace ''` |
| `read_cat` | 111.18 ms | `cat /workspace/chapters/chapter_29.md` |
| `search_rg` | 75.77 ms | `rg 卡特琳娜 /workspace/chapters/chapter_29.md` |
| `edit_replace_to_dst` | 73.92 ms | `edit /workspace/chapters/chapter_29.md --find '舔掉了膜上沾的布丁，还溅出他的体液！恶心！下流！无耻！”
 
　　主持人想解释地球人的唾液跟怀孕没关系——朱丽斯星人根本没这概念，可已经来不及了。原告女士的丈夫猛地窜上台——其实更像快速爬行，想偷袭对面正要辩解的游客被告；
 
　　这下后台全乱了，朱丽斯星人的亲戚们一窝蜂冲上台，朝着他们认定的“罪犯”猛扔粘液；
 
　　接着，一位自称柯默思星际综合大学教授的嘉宾出场，试图用他的研究证明原告和被告存在生殖隔离，所以被告的口水绝不可能导致原告怀孕。那位软体丈夫的天都塌了；
 
　　原告女士声嘶力竭地向暴怒的丈夫解释，想让他相信胚胎来自被告意外溅落的体液，而不是她出轨；可丈夫坚决要求离婚，要求她退还当初赠送给她的订婚胶质，还扬言绝不会付孩子的抚养费；
 
　　柯默思大学教授再次声明强调被告是无辜的，这让原告夫妇双方的亲戚分成了两派互殴，但谁都没忘了顺手猛揍教授；
 
　　整场闹剧里，不堪入耳的朱丽斯脏话谩骂夹杂在软体人爆鸣的共振声中。混乱中，主持人的假发被打飞，露出他精心遮掩的秃顶——他叫莫瑞，这节目就是以他命名的。
 
　　直到保安掏出对付软体星人的杀手锏——凝水剂，威胁要泼过去，这场闹剧' --replace '替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替替'` |
| `read_cat` | 71.85 ms | `cat /workspace/chapters/chapter_29.md` |
| `append_echo` | 70.09 ms | `echo marker-1 >> /workspace/chapters/chapter_29.md` |
| `append_echo` | 66.64 ms | `echo marker-4 >> /workspace/chapters/chapter_29.md` |
| `append_echo` | 65.75 ms | `echo marker-0 >> /workspace/chapters/chapter_29.md` |

## Background Context

- Flush consistency error rate: 0.0000
- Cache metrics after drain: `{"cache_bytes": 0, "cache_hit": 0, "cache_miss": 5, "dirty_entries": 0, "entries": 5, "evicted": 0, "flush_conflict": 0, "flush_error": 0, "flush_ok": 7, "workspace_dirty_nodes": 0, "write_ops": 21}`

## Interpretation

- This report treats one `run_virtual_bash()` call as one agent-visible wait event.
- Background drain time is reported separately and excluded from agent wait percentiles.
- When memory cache is enabled, this reflects what the agent waits for, not what the async flusher waits for.
