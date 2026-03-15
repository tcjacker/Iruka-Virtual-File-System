# VFS Agent Wait Report

## Scope

- Runtime architecture: Redis-backed VFS control plane at `redis://127.0.0.1:6379/0`
- PostgreSQL backend: `pgm-6we50rtg1rc9d3qtpo.pgsql.japan.rds.aliyuncs.com:5432/file_sys`
- Input file: `/Users/tc/Downloads/test.txt`
- Input size: `47870 chars / 1194 lines`
- Benchmark rule: one `run_virtual_bash()` call = one agent-visible wait event
- Workload mix:
  - `read_cat` x8
  - `append_echo` x12
  - `edit_replace_to_dst` x4
  - `edit_replace_to_src` x4
  - `edit_delete_span` x1
  - `search_rg` x8
- Warmup loops excluded from summary: `2`
- Background drain excluded from wait percentiles

## Key Result

After moving the VFS hot-path state from process memory into local Redis, agent-visible wait time stays within the target range for this workload:

- Mean: `67.22 ms`
- P50: `64.71 ms`
- P95: `84.60 ms`
- P99: `106.51 ms`
- Max: `111.81 ms`

So the practical answer is:

- `100 ms` target: mostly achieved on mean / median / P95
- tail latency: not fully closed yet, because P99 and max still cross `100 ms`

## Distribution

| Operation | Count | Mean | P50 | P95 | P99 | Max |
| --- | --- | --- | --- | --- | --- | --- |
| `read_cat` | 8 | `71.77 ms` | `64.95 ms` | `101.19 ms` | `109.69 ms` | `111.81 ms` |
| `append_echo` | 12 | `66.90 ms` | `65.35 ms` | `81.82 ms` | `94.04 ms` | `97.10 ms` |
| `edit_replace_to_dst` | 4 | `64.10 ms` | `64.19 ms` | `64.66 ms` | `64.70 ms` | `64.71 ms` |
| `edit_replace_to_src` | 4 | `66.53 ms` | `66.15 ms` | `69.15 ms` | `69.55 ms` | `69.65 ms` |
| `edit_delete_span` | 1 | `64.01 ms` | `64.01 ms` | `64.01 ms` | `64.01 ms` | `64.01 ms` |
| `search_rg` | 8 | `65.45 ms` | `64.52 ms` | `74.76 ms` | `78.08 ms` | `78.91 ms` |

## Slowest Samples

| Operation | Duration | Command |
| --- | --- | --- |
| `read_cat` | `111.81 ms` | `cat /workspace/chapters/chapter_26.md` |
| `append_echo` | `97.10 ms` | `echo marker-9 >> /workspace/chapters/chapter_26.md` |
| `read_cat` | `81.48 ms` | `cat /workspace/chapters/chapter_26.md` |
| `search_rg` | `78.91 ms` | `rg 卡特琳娜 /workspace/chapters/chapter_26.md` |

## Redis Key Design

The new key layout is tenant-aware and workspace-scoped:

- Workspace index:
  - `iruka_agent:vfs:workspace-index:{workspace_id}`
- Workspace mirror:
  - `iruka_agent:vfs:tenant:{tenant_key}:workspace:{workspace_id}:mirror`
- Workspace lock:
  - `iruka_agent:vfs:tenant:{tenant_key}:workspace:{workspace_id}:lock`
- Dirty workspace set:
  - `iruka_agent:vfs:dirty-workspaces`

Example from the smoke test:

- `iruka_agent:vfs:tenant:tenant-a:workspace:1:mirror`
- `iruka_agent:vfs:workspace-index:1`
- `iruka_agent:vfs:dirty-workspaces`

Tenant resolution rule:

- Prefer `workspace.metadata_json["tenant_id"]`
- Fallback to `workspace.metadata_json["tenant"]`
- Final fallback: `default`

This avoids collisions across tenants even when workspace ids overlap across deployments or future tenant partitions.

## What Changed

Hot-path operations no longer depend on remote PostgreSQL for each command:

1. Workspace mirror is stored in Redis instead of a process-local dict.
2. Each command loads and mutates the workspace image from Redis-backed state.
3. Path resolution and directory traversal use mirror indexes, not PG queries.
4. `cwd` and session state are updated in the mirror, not committed synchronously.
5. `rg` scans the in-memory workspace image after loading from Redis, not PG subtree queries.
6. PostgreSQL is now used as asynchronous checkpoint storage rather than synchronous control plane.

## Verification

Verified locally:

- `py_compile` passed for:
  - the main VFS service module
  - the runtime config module
  - the agent wait benchmark script
- Local Redis connectivity to `127.0.0.1:6379` passed.
- Redis-backed smoke test passed for `cat`, `append`, `rg`.

## Current Risk

Latency is now acceptable, but durability is not yet clean enough.

The latest remote benchmark finished successfully, but checkpoint metrics show:

- `flush_ok=4`
- `flush_error=13`
- `flush_consistency_error_rate=0.7647`

That means the Redis-backed hot path is working, but the asynchronous checkpoint path to PostgreSQL is still unstable under this workload. Right now the main risk is not agent wait time anymore; it is persistence correctness.

## Next Step

The next implementation step should be to harden checkpointing:

1. make Redis mirror flush atomic per workspace
2. make checkpoint retry explicit instead of periodic best-effort only
3. record checkpoint errors with enough detail to identify whether failures come from remote PG, teardown races, or stale ids
4. add a forced `flush_workspace(workspace_id)` on turn-end / milestone

Without that, the latency target is largely met, but persistence is still not production-safe.
