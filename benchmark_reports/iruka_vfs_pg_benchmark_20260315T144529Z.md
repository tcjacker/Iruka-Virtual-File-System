# iruka_vfs PostgreSQL Benchmark Report

- Started: `2026-03-15T14:45:13.460016+00:00`
- Ended: `2026-03-15T14:45:29.475008+00:00`
- Duration: `16.01s`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`
- Workspace count: `2`
- Commands per workspace: `6`
- Chapter bytes: `65536`

## Single Workspace Latency

| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ensure_warm` | 5 | 0.29 | 0.23 | 0.42 | 0.45 | 0.46 |
| `cat_chapter` | 5 | 193.21 | 144.92 | 327.46 | 359.00 | 366.88 |
| `wc_chapter` | 5 | 146.83 | 144.63 | 155.66 | 156.72 | 156.99 |
| `search_workspace` | 5 | 153.87 | 152.62 | 157.91 | 158.16 | 158.22 |
| `write_note_redirect` | 5 | 142.98 | 141.69 | 146.75 | 146.84 | 146.86 |
| `edit_chapter` | 5 | 155.03 | 151.55 | 165.59 | 166.44 | 166.66 |
| `flush` | 5 | 129.20 | 1.51 | 512.86 | 615.10 | 640.66 |
| `flush_success_rate` | 5 attempts | 100.0% | 0 failures | - | - | - |

## Concurrent Throughput

- Total commands: `12`
- Successful commands: `12`
- Wall time: `2.10s`
- Throughput: `5.72 commands/s`
- Command latency mean/p95: `176.28 / 256.55 ms`
- Flush mean/p95: `482.02 / 548.68 ms`
- Flush success rate: `100.0%`
- Flush failures: `0`
- Command log rows observed: `42`

## Cleanup

- Performed: `True`
- Tenant pattern: `bench_20260315T144513Z%`
