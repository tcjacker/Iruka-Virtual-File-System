# Edit Phase Profile Report

- Generated: `2026-03-15T15:41:17.859624+00:00`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`

## Scenario `baseline`

- Command mean/p95: `3.11 / 4.76 ms`
- Checkpoint catch-up mean/p95: `552.68 / 773.12 ms`

| Role | Phase | Count | Mean (ms) | P95 (ms) | Max (ms) | Total (ms) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `checkpoint_worker` | `mirror.flush_workspace_mirror` | 6 | 339.82 | 564.79 | 624.76 | 2038.91 |
| `main` | `service.ensure_virtual_workspace` | 7 | 219.76 | 1074.44 | 1534.37 | 1538.32 |
| `command_log_worker` | `repo.bulk_insert_command_logs` | 6 | 197.53 | 222.28 | 230.85 | 1185.16 |
| `checkpoint_worker` | `sqlalchemy.Session.scalars:sqlalchemy_repositories.py:get_node:154` | 6 | 196.35 | 356.23 | 410.92 | 1178.10 |
| `main` | `sqlalchemy.Session.flush:unknown` | 1 | 149.12 | 149.12 | 149.12 | 149.12 |
| `command_log_worker` | `sqlalchemy.Session.execute:sqlalchemy_repositories.py:bulk_insert_command_logs:266` | 6 | 148.43 | 173.01 | 182.80 | 890.60 |
| `main` | `sqlalchemy.Session.scalars:sqlalchemy_repositories.py:list_workspace_nodes:163` | 1 | 142.21 | 142.21 | 142.21 | 142.21 |
| `checkpoint_worker` | `sqlalchemy.Session.commit:workspace_mirror.py:flush_workspace_mirror:808` | 6 | 137.51 | 202.48 | 207.93 | 825.06 |
| `main` | `sqlalchemy.Session.flush:sqlalchemy_repositories.py:create_session:75` | 1 | 100.27 | 100.27 | 100.27 | 100.27 |
| `main` | `sqlalchemy.Session.execute:unknown` | 11 | 95.65 | 430.17 | 708.88 | 1052.19 |
| `main` | `sqlalchemy.Session.commit:unknown` | 3 | 95.12 | 179.05 | 193.65 | 285.37 |
| `checkpoint_worker` | `sqlalchemy.Session.flush:workspace_mirror.py:flush_workspace_mirror:808` | 6 | 92.11 | 154.89 | 159.50 | 552.64 |

