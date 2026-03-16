# iruka_vfs PostgreSQL Benchmark Report

- Started: `2026-03-15T15:40:28.858663+00:00`
- Ended: `2026-03-15T15:40:53.201832+00:00`
- Duration: `24.34s`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`
- Workspace count: `8`
- Commands per workspace: `24`
- Chapter bytes: `65536`

## Single Workspace Latency

| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ensure_warm` | 20 | 0.31 | 0.30 | 0.36 | 0.41 | 0.42 |
| `cat_chapter` | 20 | 1.13 | 1.09 | 1.28 | 1.36 | 1.38 |
| `wc_chapter` | 20 | 1.07 | 1.02 | 1.20 | 1.83 | 1.98 |
| `search_workspace` | 20 | 6.91 | 6.85 | 7.16 | 7.94 | 8.13 |
| `write_note_redirect` | 20 | 1.26 | 1.24 | 1.56 | 1.59 | 1.59 |
| `edit_chapter` | 20 | 1.65 | 1.59 | 1.84 | 1.87 | 1.88 |
| `flush` | 20 | 99.55 | 3.27 | 103.71 | 1559.55 | 1923.51 |
| `flush_success_rate` | 20 attempts | 100.0% | 0 failures | - | - | - |

## Concurrent Throughput

- Total commands: `192`
- Successful commands: `192`
- Wall time: `0.98s`
- Throughput: `195.07 commands/s`
- Command latency mean/p95: `13.34 / 25.45 ms`
- Flush mean/p95: `521.26 / 598.44 ms`
- Flush success rate: `100.0%`
- Flush failures: `0`
- Command log rows observed: `211`

## Cleanup

- Performed: `True`
- Tenant pattern: `bench_20260315T154028Z%`
