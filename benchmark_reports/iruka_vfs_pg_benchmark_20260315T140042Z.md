# iruka_vfs PostgreSQL Benchmark Report

- Started: `2026-03-15T13:59:25.677591+00:00`
- Ended: `2026-03-15T14:00:42.989258+00:00`
- Duration: `77.31s`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`
- Workspace count: `8`
- Commands per workspace: `24`
- Chapter bytes: `65536`

## Single Workspace Latency

| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ensure_warm` | 20 | 0.27 | 0.19 | 0.65 | 0.74 | 0.77 |
| `cat_chapter` | 20 | 146.61 | 146.84 | 152.39 | 160.80 | 162.90 |
| `wc_chapter` | 20 | 145.04 | 143.72 | 156.65 | 159.84 | 160.64 |
| `search_workspace` | 20 | 155.69 | 155.25 | 162.06 | 164.65 | 165.30 |
| `write_note_redirect` | 20 | 143.63 | 143.12 | 148.49 | 148.93 | 149.04 |
| `edit_chapter` | 20 | 154.69 | 144.97 | 167.11 | 304.74 | 339.14 |
| `flush` | 0 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| `flush_success_rate` | 20 attempts | 0.0% | 20 failures | - | - | - |

## Concurrent Throughput

- Total commands: `192`
- Successful commands: `192`
- Wall time: `6.97s`
- Throughput: `27.55 commands/s`
- Command latency mean/p95: `157.29 / 174.28 ms`
- Flush mean/p95: `0.00 / 0.00 ms`
- Flush success rate: `0.0%`
- Flush failures: `8`
- Command log rows observed: `0`

## Cleanup

- Performed: `True`
- Tenant pattern: `bench_20260315T135925Z%`
