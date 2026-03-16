# iruka_vfs PostgreSQL Benchmark Report

- Started: `2026-03-15T14:16:11.623928+00:00`
- Ended: `2026-03-15T14:17:06.950977+00:00`
- Duration: `55.33s`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`
- Workspace count: `8`
- Commands per workspace: `24`
- Chapter bytes: `65536`

## Single Workspace Latency

| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ensure_warm` | 20 | 0.31 | 0.22 | 0.57 | 0.59 | 0.60 |
| `cat_chapter` | 20 | 165.35 | 163.36 | 174.81 | 218.37 | 229.26 |
| `wc_chapter` | 20 | 159.67 | 161.31 | 168.80 | 172.69 | 173.66 |
| `search_workspace` | 20 | 170.96 | 172.91 | 179.95 | 180.15 | 180.20 |
| `write_note_redirect` | 20 | 173.08 | 170.80 | 218.31 | 218.43 | 218.45 |
| `edit_chapter` | 20 | 337.76 | 266.30 | 599.73 | 893.78 | 967.29 |
| `flush` | 20 | 6.03 | 1.87 | 7.33 | 68.92 | 84.32 |
| `flush_success_rate` | 20 attempts | 100.0% | 0 failures | - | - | - |

## Concurrent Throughput

- Total commands: `192`
- Successful commands: `192`
- Wall time: `6.52s`
- Throughput: `29.46 commands/s`
- Command latency mean/p95: `186.63 / 368.91 ms`
- Flush mean/p95: `378.25 / 588.82 ms`
- Flush success rate: `100.0%`
- Flush failures: `0`
- Command log rows observed: `305`

## Cleanup

- Performed: `True`
- Tenant pattern: `bench_20260315T141611Z%`
