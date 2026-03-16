# Edit Phase Profile Report

- Generated: `2026-03-15T15:34:38.902569+00:00`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`

## Scenario `baseline`

- Command mean/p95: `170.44 / 177.37 ms`
- Checkpoint catch-up mean/p95: `419.70 / 499.42 ms`

| Role | Phase | Count | Mean (ms) | P95 (ms) | Max (ms) | Total (ms) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `main` | `service.ensure_virtual_workspace` | 3 | 587.03 | 1583.79 | 1759.63 | 1761.08 |
| `checkpoint_worker` | `mirror.flush_workspace_mirror` | 2 | 458.85 | 573.25 | 585.96 | 917.70 |
| `checkpoint_worker` | `sqlalchemy.Session.scalars:sqlalchemy_repositories.py:get_node:154` | 2 | 285.07 | 374.69 | 384.65 | 570.13 |
| `command_log_worker` | `repo.bulk_insert_command_logs` | 2 | 258.36 | 323.52 | 330.76 | 516.72 |
| `command_log_worker` | `sqlalchemy.Session.execute:sqlalchemy_repositories.py:bulk_insert_command_logs:266` | 2 | 212.38 | 277.25 | 284.45 | 424.77 |
| `main` | `sqlalchemy.Session.flush:unknown` | 1 | 179.01 | 179.01 | 179.01 | 179.01 |
| `checkpoint_worker` | `sqlalchemy.Session.commit:workspace_mirror.py:flush_workspace_mirror:798` | 2 | 172.38 | 197.05 | 199.79 | 344.76 |
| `main` | `service.run_virtual_bash` | 2 | 170.42 | 177.34 | 178.11 | 340.84 |
| `main` | `sqlalchemy.Session.scalars:sqlalchemy_repositories.py:list_workspace_nodes:163` | 1 | 168.21 | 168.21 | 168.21 | 168.21 |
| `main` | `sqlalchemy.Session.scalars:paths.py:node_path:163` | 2 | 166.65 | 171.32 | 171.84 | 333.31 |
| `checkpoint_worker` | `sqlalchemy.Session.flush:workspace_mirror.py:flush_workspace_mirror:798` | 2 | 123.72 | 147.72 | 150.38 | 247.44 |
| `main` | `sqlalchemy.Session.commit:unknown` | 3 | 116.61 | 216.84 | 234.20 | 349.84 |

