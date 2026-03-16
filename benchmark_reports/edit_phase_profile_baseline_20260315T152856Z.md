# Edit Phase Profile Report

- Generated: `2026-03-15T15:28:56.503221+00:00`
- Database host: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com`
- Database name: `file_sys`

## Scenario `baseline`

- Command mean/p95: `151.03 / 157.79 ms`
- Checkpoint catch-up mean/p95: `327.11 / 458.60 ms`

| Role | Phase | Count | Mean (ms) | P95 (ms) | Max (ms) | Total (ms) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `checkpoint_worker` | `mirror.flush_workspace_mirror` | 12 | 323.06 | 467.86 | 577.19 | 3876.70 |
| `command_log_worker` | `repo.bulk_insert_command_logs` | 12 | 240.22 | 328.60 | 387.88 | 2882.69 |
| `main` | `repo.create_node` | 9 | 68.43 | 143.13 | 201.19 | 615.91 |
| `checkpoint_worker` | `redis.blpop` | 24 | 3.34 | 15.13 | 17.32 | 80.14 |
| `main` | `mirror.load_workspace_mirror` | 346 | 1.16 | 4.05 | 11.63 | 401.80 |
| `checkpoint_worker` | `mirror.load_workspace_mirror` | 36 | 0.74 | 1.52 | 3.92 | 26.57 |
| `checkpoint_worker` | `mirror.set_workspace_mirror` | 12 | 0.59 | 1.80 | 2.81 | 7.13 |
| `main` | `service.set_workspace_mirror` | 13 | 0.33 | 0.67 | 0.72 | 4.26 |
| `main` | `service.run_command_chain` | 12 | 0.22 | 0.37 | 0.49 | 2.63 |
| `main` | `service.exec_edit` | 12 | 0.11 | 0.19 | 0.28 | 1.37 |
| `checkpoint_worker` | `redis.rpush` | 12 | 0.04 | 0.17 | 0.31 | 0.54 |
| `main` | `mirror.enqueue_checkpoint` | 12 | 0.02 | 0.05 | 0.06 | 0.29 |

