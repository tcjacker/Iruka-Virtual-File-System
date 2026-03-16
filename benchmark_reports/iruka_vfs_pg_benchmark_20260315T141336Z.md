# iruka_vfs PostgreSQL Benchmark Report

- Started: `2026-03-15T14:13:17.766506+00:00`
- Ended: `2026-03-15T14:13:36.176049+00:00`
- Duration: `18.41s`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`
- Workspace count: `2`
- Commands per workspace: `6`
- Chapter bytes: `65536`

## Single Workspace Latency

| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ensure_warm` | 5 | 0.27 | 0.24 | 0.33 | 0.33 | 0.33 |
| `cat_chapter` | 5 | 194.63 | 161.96 | 298.24 | 325.21 | 331.95 |
| `wc_chapter` | 5 | 162.83 | 163.43 | 165.54 | 165.73 | 165.77 |
| `search_workspace` | 5 | 207.36 | 171.81 | 326.08 | 356.48 | 364.08 |
| `write_note_redirect` | 5 | 161.66 | 165.13 | 169.05 | 169.65 | 169.81 |
| `edit_chapter` | 5 | 400.25 | 499.19 | 535.22 | 538.90 | 539.82 |
| `flush` | 5 | 105.84 | 1.78 | 418.67 | 501.96 | 522.78 |
| `flush_success_rate` | 5 attempts | 100.0% | 0 failures | - | - | - |

## Concurrent Throughput

- Total commands: `12`
- Successful commands: `12`
- Wall time: `1.78s`
- Throughput: `6.73 commands/s`
- Command latency mean/p95: `163.88 / 208.33 ms`
- Flush mean/p95: `402.27 / 405.37 ms`
- Flush success rate: `100.0%`
- Flush failures: `0`
- Command log rows observed: `42`

## Cleanup

- Performed: `True`
- Tenant pattern: `bench_20260315T141317Z%`
