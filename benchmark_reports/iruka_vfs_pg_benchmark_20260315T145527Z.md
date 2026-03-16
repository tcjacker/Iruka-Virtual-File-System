# iruka_vfs PostgreSQL Benchmark Report

- Started: `2026-03-15T14:54:29.261415+00:00`
- Ended: `2026-03-15T14:55:27.128262+00:00`
- Duration: `57.87s`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`
- Workspace count: `2`
- Commands per workspace: `6`
- Chapter bytes: `65536`

## Single Workspace Latency

| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ensure_warm` | 5 | 0.25 | 0.22 | 0.33 | 0.34 | 0.34 |
| `cat_chapter` | 5 | 270.93 | 212.56 | 511.55 | 566.82 | 580.64 |
| `wc_chapter` | 5 | 214.57 | 178.97 | 333.35 | 354.39 | 359.65 |
| `search_workspace` | 5 | 201.65 | 213.19 | 224.67 | 226.87 | 227.43 |
| `write_note_redirect` | 5 | 439.82 | 491.39 | 705.99 | 730.84 | 737.05 |
| `edit_chapter` | 5 | 2753.88 | 764.96 | 6559.77 | 6675.89 | 6704.92 |
| `flush` | 5 | 206.40 | 0.96 | 822.58 | 986.85 | 1027.92 |
| `flush_success_rate` | 5 attempts | 100.0% | 0 failures | - | - | - |

## Concurrent Throughput

- Total commands: `12`
- Successful commands: `12`
- Wall time: `3.07s`
- Throughput: `3.91 commands/s`
- Command latency mean/p95: `254.14 / 516.58 ms`
- Flush mean/p95: `762.61 / 913.15 ms`
- Flush success rate: `100.0%`
- Flush failures: `0`
- Command log rows observed: `42`

## Cleanup

- Performed: `True`
- Tenant pattern: `bench_20260315T145429Z%`
