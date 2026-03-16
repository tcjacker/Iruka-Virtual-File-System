# iruka_vfs PostgreSQL Benchmark Report

- Started: `2026-03-15T14:45:48.063246+00:00`
- Ended: `2026-03-15T14:47:08.814989+00:00`
- Duration: `80.75s`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`
- Workspace count: `8`
- Commands per workspace: `24`
- Chapter bytes: `65536`

## Single Workspace Latency

| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ensure_warm` | 20 | 0.62 | 0.24 | 1.30 | 4.72 | 5.58 |
| `cat_chapter` | 20 | 229.89 | 227.46 | 319.01 | 444.00 | 475.25 |
| `wc_chapter` | 20 | 216.38 | 229.27 | 248.00 | 307.62 | 322.53 |
| `search_workspace` | 20 | 218.36 | 229.31 | 291.47 | 316.59 | 322.87 |
| `write_note_redirect` | 20 | 387.68 | 402.44 | 526.14 | 527.29 | 527.58 |
| `edit_chapter` | 20 | 568.47 | 567.11 | 954.18 | 996.43 | 1006.99 |
| `flush` | 20 | 16.38 | 1.43 | 16.67 | 242.83 | 299.36 |
| `flush_success_rate` | 20 attempts | 100.0% | 0 failures | - | - | - |

## Concurrent Throughput

- Total commands: `192`
- Successful commands: `192`
- Wall time: `9.90s`
- Throughput: `19.39 commands/s`
- Command latency mean/p95: `254.61 / 472.59 ms`
- Flush mean/p95: `643.74 / 1141.68 ms`
- Flush success rate: `100.0%`
- Flush failures: `0`
- Command log rows observed: `305`

## Cleanup

- Performed: `True`
- Tenant pattern: `bench_20260315T144548Z%`
