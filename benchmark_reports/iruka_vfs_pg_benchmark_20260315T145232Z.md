# iruka_vfs PostgreSQL Benchmark Report

- Started: `2026-03-15T14:52:08.997362+00:00`
- Ended: `2026-03-15T14:52:32.299978+00:00`
- Duration: `23.30s`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`
- Workspace count: `2`
- Commands per workspace: `6`
- Chapter bytes: `65536`

## Single Workspace Latency

| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ensure_warm` | 5 | 0.86 | 0.67 | 1.37 | 1.48 | 1.51 |
| `cat_chapter` | 5 | 272.62 | 258.68 | 472.69 | 514.36 | 524.78 |
| `wc_chapter` | 5 | 196.34 | 164.85 | 254.84 | 255.77 | 256.01 |
| `search_workspace` | 5 | 211.23 | 211.21 | 256.98 | 262.70 | 264.13 |
| `write_note_redirect` | 5 | 302.07 | 295.76 | 433.14 | 435.63 | 436.26 |
| `edit_chapter` | 5 | 639.84 | 577.92 | 860.41 | 885.93 | 892.31 |
| `flush` | 5 | 76.76 | 1.39 | 302.86 | 363.01 | 378.05 |
| `flush_success_rate` | 5 attempts | 100.0% | 0 failures | - | - | - |

## Concurrent Throughput

- Total commands: `12`
- Successful commands: `12`
- Wall time: `2.27s`
- Throughput: `5.28 commands/s`
- Command latency mean/p95: `216.63 / 375.75 ms`
- Flush mean/p95: `421.40 / 480.88 ms`
- Flush success rate: `100.0%`
- Flush failures: `0`
- Command log rows observed: `42`

## Cleanup

- Performed: `True`
- Tenant pattern: `bench_20260315T145208Z%`
