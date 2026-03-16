# Edit Phase Profile Report

- Generated: `2026-03-15T15:37:48.755951+00:00`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`

## Scenario `baseline`

- Command mean/p95: `2.10 / 2.75 ms`
- Checkpoint catch-up mean/p95: `561.37 / 741.07 ms`

| Role | Phase | Count | Mean (ms) | P95 (ms) | Max (ms) | Total (ms) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `checkpoint_worker` | `mirror.flush_workspace_mirror` | 4 | 350.43 | 530.64 | 569.62 | 1401.73 |
| `main` | `service.ensure_virtual_workspace` | 5 | 310.61 | 1240.86 | 1550.78 | 1553.07 |
| `checkpoint_worker` | `sqlalchemy.Session.scalars:sqlalchemy_repositories.py:get_node:154` | 4 | 208.52 | 343.52 | 376.78 | 834.09 |
| `command_log_worker` | `repo.bulk_insert_command_logs` | 4 | 203.48 | 207.39 | 207.89 | 813.93 |
| `main` | `sqlalchemy.Session.flush:unknown` | 1 | 162.67 | 162.67 | 162.67 | 162.67 |
| `command_log_worker` | `sqlalchemy.Session.execute:sqlalchemy_repositories.py:bulk_insert_command_logs:266` | 4 | 153.85 | 158.83 | 159.77 | 615.39 |
| `main` | `sqlalchemy.Session.scalars:sqlalchemy_repositories.py:list_workspace_nodes:163` | 1 | 152.93 | 152.93 | 152.93 | 152.93 |
| `checkpoint_worker` | `sqlalchemy.Session.commit:workspace_mirror.py:flush_workspace_mirror:798` | 4 | 139.68 | 185.68 | 191.46 | 558.72 |
| `main` | `sqlalchemy.Session.execute:unknown` | 11 | 121.68 | 567.14 | 982.03 | 1338.47 |
| `main` | `sqlalchemy.Session.commit:unknown` | 3 | 103.19 | 195.43 | 211.67 | 309.57 |
| `main` | `sqlalchemy.Session.flush:sqlalchemy_repositories.py:create_session:75` | 1 | 98.52 | 98.52 | 98.52 | 98.52 |
| `checkpoint_worker` | `sqlalchemy.Session.flush:workspace_mirror.py:flush_workspace_mirror:798` | 4 | 90.21 | 139.42 | 145.84 | 360.83 |

