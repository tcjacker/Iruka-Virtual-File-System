# Edit Phase Profile Report

- Generated: `2026-03-15T15:33:15.072696+00:00`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`

## Scenario `baseline`

- Command mean/p95: `157.34 / 159.36 ms`
- Checkpoint catch-up mean/p95: `354.37 / 481.77 ms`

| Role | Phase | Count | Mean (ms) | P95 (ms) | Max (ms) | Total (ms) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `checkpoint_worker` | `mirror.flush_workspace_mirror` | 6 | 360.97 | 519.88 | 568.13 | 2165.85 |
| `command_log_worker` | `repo.bulk_insert_command_logs` | 6 | 250.22 | 355.81 | 384.31 | 1501.35 |
| `main` | `service.ensure_virtual_workspace` | 7 | 237.17 | 1161.08 | 1658.52 | 1660.22 |
| `checkpoint_worker` | `sqlalchemy.Session.scalars` | 6 | 224.85 | 341.12 | 378.34 | 1349.11 |
| `command_log_worker` | `sqlalchemy.Session.execute` | 6 | 196.64 | 301.28 | 330.37 | 1179.83 |
| `main` | `service.run_virtual_bash` | 6 | 157.33 | 159.35 | 159.39 | 943.95 |
| `checkpoint_worker` | `sqlalchemy.Session.commit` | 6 | 132.05 | 180.41 | 185.51 | 792.33 |
| `main` | `sqlalchemy.Session.execute` | 11 | 109.01 | 492.67 | 821.14 | 1199.08 |
| `main` | `sqlalchemy.Session.commit` | 3 | 103.40 | 192.11 | 207.67 | 310.19 |
| `main` | `sqlalchemy.Session.scalars` | 20 | 91.42 | 156.83 | 157.05 | 1828.34 |
| `checkpoint_worker` | `sqlalchemy.Session.flush` | 6 | 85.24 | 134.22 | 139.13 | 511.45 |
| `main` | `sqlalchemy.Session.flush` | 11 | 82.51 | 184.29 | 211.50 | 907.64 |

