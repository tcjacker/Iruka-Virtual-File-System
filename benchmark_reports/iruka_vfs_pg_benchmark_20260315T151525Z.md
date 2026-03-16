# iruka_vfs PostgreSQL Benchmark Report

- Started: `2026-03-15T15:14:19.785629+00:00`
- Ended: `2026-03-15T15:15:25.419070+00:00`
- Duration: `65.63s`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`
- Workspace count: `8`
- Commands per workspace: `24`
- Chapter bytes: `65536`

## Single Workspace Latency

| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ensure_warm` | 20 | 0.47 | 0.49 | 0.60 | 0.60 | 0.61 |
| `cat_chapter` | 20 | 202.51 | 172.10 | 254.72 | 432.02 | 476.35 |
| `wc_chapter` | 20 | 168.71 | 164.24 | 205.98 | 232.57 | 239.22 |
| `search_workspace` | 20 | 172.22 | 171.43 | 180.42 | 182.51 | 183.03 |
| `write_note_redirect` | 20 | 317.52 | 319.91 | 469.43 | 479.05 | 481.45 |
| `edit_chapter` | 20 | 438.82 | 485.36 | 650.08 | 743.99 | 767.47 |
| `flush` | 20 | 17.57 | 2.58 | 18.40 | 243.98 | 300.38 |
| `flush_success_rate` | 20 attempts | 100.0% | 0 failures | - | - | - |

## Concurrent Throughput

- Total commands: `192`
- Successful commands: `192`
- Wall time: `7.02s`
- Throughput: `27.33 commands/s`
- Command latency mean/p95: `194.25 / 262.22 ms`
- Flush mean/p95: `442.60 / 542.36 ms`
- Flush success rate: `100.0%`
- Flush failures: `0`
- Command log rows observed: `301`

## Cleanup

- Performed: `True`
- Tenant pattern: `bench_20260315T151419Z%`
