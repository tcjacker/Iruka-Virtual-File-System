-- PostgreSQL search acceleration for virtual file system
-- Run this after virtual_file_nodes table is created.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Tree traversal / path resolution hot paths
CREATE INDEX IF NOT EXISTS idx_virtual_file_nodes_workspace_parent
ON virtual_file_nodes (workspace_id, parent_id);

CREATE INDEX IF NOT EXISTS idx_virtual_file_nodes_workspace_name
ON virtual_file_nodes (workspace_id, name);

-- Content search acceleration (substring / LIKE / ILIKE)
CREATE INDEX IF NOT EXISTS idx_virtual_file_nodes_content_trgm
ON virtual_file_nodes
USING gin (content_text gin_trgm_ops);

