# iruka_vfs PostgreSQL Benchmark Report

- Started: `2026-03-15T14:34:29.764630+00:00`
- Ended: `2026-03-15T14:35:33.087497+00:00`
- Duration: `63.32s`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`
- Workspace count: `8`
- Commands per workspace: `24`
- Chapter bytes: `65536`

## Single Workspace Latency

| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ensure_warm` | 20 | 0.27 | 0.25 | 0.33 | 0.46 | 0.49 |
| `cat_chapter` | 20 | 173.50 | 153.09 | 356.44 | 379.08 | 384.74 |
| `wc_chapter` | 20 | 164.32 | 154.58 | 196.00 | 323.68 | 355.60 |
| `search_workspace` | 20 | 171.16 | 162.46 | 202.59 | 328.23 | 359.64 |
| `write_note_redirect` | 20 | 265.12 | 164.46 | 468.00 | 599.60 | 632.50 |
| `edit_chapter` | 20 | 516.56 | 480.36 | 1155.82 | 1186.66 | 1194.37 |
| `flush` | 20 | 49.83 | 1.62 | 52.21 | 781.02 | 963.22 |
| `flush_success_rate` | 20 attempts | 100.0% | 0 failures | - | - | - |

## Concurrent Throughput

- Total commands: `192`
- Successful commands: `192`
- Wall time: `6.83s`
- Throughput: `28.11 commands/s`
- Command latency mean/p95: `174.80 / 367.45 ms`
- Flush mean/p95: `381.56 / 426.41 ms`
- Flush success rate: `100.0%`
- Flush failures: `0`
- Command log rows observed: `305`

## Cleanup

- Performed: `True`
- Tenant pattern: `bench_20260315T143429Z%`
