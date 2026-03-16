# Edit Phase Profile Report

- Generated: `2026-03-15T15:29:50.691730+00:00`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`

## Scenario `baseline`

- Command mean/p95: `150.57 / 164.17 ms`
- Checkpoint catch-up mean/p95: `331.35 / 462.65 ms`

| Role | Phase | Count | Mean (ms) | P95 (ms) | Max (ms) | Total (ms) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `checkpoint_worker` | `mirror.flush_workspace_mirror` | 12 | 322.98 | 469.90 | 573.92 | 3875.82 |
| `command_log_worker` | `repo.bulk_insert_command_logs` | 12 | 228.08 | 316.13 | 389.17 | 2736.93 |
| `main` | `service.run_virtual_bash` | 12 | 150.56 | 164.16 | 173.31 | 1806.67 |
| `main` | `service.ensure_virtual_workspace` | 13 | 119.17 | 617.97 | 1542.71 | 1549.20 |
| `main` | `repo.create_node` | 9 | 66.42 | 141.43 | 201.62 | 597.75 |
| `checkpoint_worker` | `redis.blpop` | 24 | 4.46 | 15.54 | 19.82 | 106.99 |
| `main` | `mirror.load_workspace_mirror` | 352 | 1.11 | 3.80 | 6.68 | 392.06 |
| `checkpoint_worker` | `mirror.load_workspace_mirror` | 36 | 0.88 | 2.69 | 4.14 | 31.67 |
| `checkpoint_worker` | `mirror.set_workspace_mirror` | 12 | 0.52 | 1.16 | 1.63 | 6.30 |
| `main` | `service.run_command_chain` | 12 | 0.45 | 1.50 | 2.62 | 5.44 |
| `main` | `service.set_workspace_mirror` | 13 | 0.41 | 1.12 | 2.01 | 5.28 |
| `main` | `service.exec_edit` | 12 | 0.31 | 1.22 | 2.42 | 3.77 |

