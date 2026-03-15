# Technical Docs

This directory keeps the original technical notes and benchmark records related to the VFS implementation.

## Included Documents

- [`vfs_implementation_analysis.md`](./vfs_implementation_analysis.md)
  - Original implementation analysis of the database-backed virtual filesystem, command model, cache path, and checkpoint flow.
- [`vfs_agent_wait_report.md`](./vfs_agent_wait_report.md)
  - Agent-visible wait-time benchmark report for the Redis-backed hot path.
- [`vfs_agent_wait_report_keep_data.md`](./vfs_agent_wait_report_keep_data.md)
  - Extended benchmark record with retained data for debugging and durability analysis.

## SQL Artifacts

- [`../sql/virtual_fs_pg_search_indexes.sql`](../sql/virtual_fs_pg_search_indexes.sql)
  - PostgreSQL search index setup used by the original VFS implementation.

## Notes

Some documents were written when the VFS still lived inside the original PoC backend. They are preserved here as historical implementation records.
