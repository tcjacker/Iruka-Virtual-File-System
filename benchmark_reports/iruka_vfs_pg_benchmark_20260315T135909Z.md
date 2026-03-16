# iruka_vfs PostgreSQL Benchmark Report

- Started: `2026-03-15T13:58:44.186195+00:00`
- Ended: `2026-03-15T13:59:09.162603+00:00`
- Duration: `24.98s`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`
- Workspace count: `2`
- Commands per workspace: `6`
- Chapter bytes: `65536`

## Single Workspace Latency

| Scenario | Count | Mean (ms) | Median (ms) | P95 (ms) | P99 (ms) | Max (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ensure_warm` | 5 | 0.90 | 1.00 | 1.24 | 1.25 | 1.25 |
| `cat_chapter` | 5 | 149.08 | 149.27 | 151.25 | 151.64 | 151.73 |
| `wc_chapter` | 5 | 149.86 | 147.84 | 157.01 | 158.78 | 159.22 |
| `search_workspace` | 5 | 158.40 | 157.90 | 163.27 | 163.98 | 164.16 |
| `write_note_redirect` | 5 | 150.26 | 148.34 | 159.77 | 161.41 | 161.82 |
| `edit_chapter` | 5 | 152.59 | 153.14 | 161.05 | 162.03 | 162.28 |
| `flush` | 0 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

## Concurrent Throughput

- Total commands: `12`
- Successful commands: `12`
- Wall time: `2.95s`
- Throughput: `4.07 commands/s`
- Command latency mean/p95: `175.34 / 237.79 ms`
- Flush mean/p95: `0.00 / 0.00 ms`

## Cleanup

- Performed: `True`
- Tenant pattern: `bench_20260315T135844Z%`
