# Logging Impact Experiment

- Generated: `2026-03-15T15:05:56.138605+00:00`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`

| Scenario | Edit Mean (ms) | Edit P95 (ms) | Cat Mean (ms) | Flush Mean (ms) | Concurrency QPS | Concurrency P95 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `full_logging` | 571.28 | 1044.64 | 234.77 | 118.31 | 2.55 | 341.38 |
| `slim_logging` | 552.01 | 932.36 | 251.87 | 176.70 | 2.38 | 360.15 |
| `no_log_write` | 695.57 | 1070.07 | 333.23 | 89.62 | 2.63 | 341.48 |
| `checkpoint_off` | 162.51 | 210.15 | 165.99 | 0.00 | 3.73 | 273.76 |
