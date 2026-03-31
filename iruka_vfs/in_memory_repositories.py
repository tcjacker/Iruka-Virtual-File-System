from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any

from iruka_vfs.repositories import VFSRepositories


def _construct_row(row_type: type[Any], **payload: Any) -> Any:
    annotations = getattr(row_type, "__annotations__", {})
    if annotations:
        filtered = {key: value for key, value in payload.items() if key in annotations}
        return row_type(**filtered)
    try:
        return row_type(**payload)
    except TypeError:
        filtered = {}
        for key, value in payload.items():
            if hasattr(row_type, key):
                filtered[key] = value
        return row_type(**filtered)


@dataclass
class _InMemoryRepoState:
    next_session_id: int = 1
    next_node_id: int = 1
    next_command_log_id: int = 1
    workspaces: dict[tuple[str, int], Any] | None = None
    sessions: dict[int, Any] | None = None
    nodes: dict[int, Any] | None = None
    command_logs: dict[int, dict[str, Any]] | None = None
    lock: Lock | None = None

    def __post_init__(self) -> None:
        if self.workspaces is None:
            self.workspaces = {}
        if self.sessions is None:
            self.sessions = {}
        if self.nodes is None:
            self.nodes = {}
        if self.command_logs is None:
            self.command_logs = {}
        if self.lock is None:
            self.lock = Lock()


@dataclass(frozen=True)
class InMemoryWorkspaceRepository:
    AgentWorkspace: type[Any]
    state: _InMemoryRepoState

    def get_workspace(self, db, workspace_id: int, tenant_key: str) -> Any | None:
        return self.state.workspaces.get((tenant_key, int(workspace_id)))

    def update_workspace_metadata(
        self,
        db,
        *,
        workspace_id: int,
        tenant_key: str,
        metadata_json: dict[str, Any],
    ) -> bool:
        workspace = self.get_workspace(db, workspace_id, tenant_key)
        if workspace is None:
            workspace = _construct_row(
                self.AgentWorkspace,
                id=int(workspace_id),
                tenant_id=tenant_key,
                metadata_json=dict(metadata_json),
            )
            self.state.workspaces[(tenant_key, int(workspace_id))] = workspace
        workspace.metadata_json = dict(metadata_json)
        if hasattr(workspace, "tenant_id"):
            workspace.tenant_id = tenant_key
        return True


@dataclass(frozen=True)
class InMemorySessionRepository:
    VirtualShellSession: type[Any]
    state: _InMemoryRepoState

    def get_active_session(self, db, workspace_id: int, tenant_key: str) -> Any | None:
        sessions = [
            session
            for session in self.state.sessions.values()
            if int(getattr(session, "workspace_id", 0)) == int(workspace_id)
            and str(getattr(session, "tenant_id", "") or "") == tenant_key
            and str(getattr(session, "status", "")) == "active"
        ]
        sessions.sort(key=lambda item: int(getattr(item, "id", 0)), reverse=True)
        return sessions[0] if sessions else None

    def create_session(
        self,
        db,
        *,
        tenant_key: str,
        workspace_id: int,
        cwd_node_id: int,
        env_json: dict[str, Any],
        status: str,
    ) -> Any:
        with self.state.lock:
            session_id = self.state.next_session_id
            self.state.next_session_id += 1
        session = _construct_row(
            self.VirtualShellSession,
            id=session_id,
            tenant_id=tenant_key,
            workspace_id=int(workspace_id),
            cwd_node_id=int(cwd_node_id),
            env_json=dict(env_json),
            status=status,
        )
        if hasattr(session, "created_at"):
            session.created_at = datetime.utcnow()
        if hasattr(session, "updated_at"):
            session.updated_at = datetime.utcnow()
        self.state.sessions[int(session_id)] = session
        return session

    def update_session_cwd(
        self,
        db,
        *,
        session_id: int,
        tenant_key: str,
        cwd_node_id: int,
    ) -> None:
        session = self.state.sessions.get(int(session_id))
        if not session or str(getattr(session, "tenant_id", "") or "") != tenant_key:
            return
        session.cwd_node_id = int(cwd_node_id)
        if hasattr(session, "updated_at"):
            session.updated_at = datetime.utcnow()


@dataclass(frozen=True)
class InMemoryNodeRepository:
    VirtualFileNode: type[Any]
    state: _InMemoryRepoState

    def get_root(self, db, workspace_id: int, tenant_key: str) -> Any | None:
        return next(
            (
                node
                for node in self.state.nodes.values()
                if str(getattr(node, "tenant_id", "") or "") == tenant_key
                and int(getattr(node, "workspace_id", 0)) == int(workspace_id)
                and getattr(node, "parent_id", None) is None
                and str(getattr(node, "name", "") or "") == ""
            ),
            None,
        )

    def get_child(
        self,
        db,
        *,
        tenant_key: str,
        workspace_id: int,
        parent_id: int,
        name: str,
        node_type: str,
    ) -> Any | None:
        return next(
            (
                node
                for node in self.state.nodes.values()
                if str(getattr(node, "tenant_id", "") or "") == tenant_key
                and int(getattr(node, "workspace_id", 0)) == int(workspace_id)
                and int(getattr(node, "parent_id", -1) or -1) == int(parent_id)
                and str(getattr(node, "name", "") or "") == name
                and str(getattr(node, "node_type", "") or "") == node_type
            ),
            None,
        )

    def create_node(
        self,
        db,
        *,
        tenant_key: str,
        workspace_id: int,
        parent_id: int | None,
        name: str,
        node_type: str,
        content_text: str,
        version_no: int,
    ) -> Any:
        with self.state.lock:
            node_id = self.state.next_node_id
            self.state.next_node_id += 1
        now = datetime.utcnow()
        node = _construct_row(
            self.VirtualFileNode,
            id=node_id,
            tenant_id=tenant_key,
            workspace_id=int(workspace_id),
            parent_id=parent_id,
            name=name,
            node_type=node_type,
            content_text=content_text,
            version_no=int(version_no),
            created_at=now,
            updated_at=now,
        )
        self.state.nodes[int(node_id)] = node
        return node

    def get_node(self, db, node_id: int, tenant_key: str) -> Any | None:
        node = self.state.nodes.get(int(node_id))
        if not node or str(getattr(node, "tenant_id", "") or "") != tenant_key:
            return None
        return node

    def list_workspace_nodes(self, db, workspace_id: int, tenant_key: str) -> list[Any]:
        nodes = [
            node
            for node in self.state.nodes.values()
            if str(getattr(node, "tenant_id", "") or "") == tenant_key
            and int(getattr(node, "workspace_id", 0)) == int(workspace_id)
        ]
        nodes.sort(
            key=lambda item: (
                -1 if getattr(item, "parent_id", None) is None else int(item.parent_id),
                str(getattr(item, "node_type", "") or ""),
                str(getattr(item, "name", "") or ""),
            )
        )
        return nodes

    def update_node_content(
        self,
        db,
        *,
        node_id: int,
        tenant_key: str,
        parent_id: int | None,
        name: str,
        node_type: str,
        content_text: str,
        version_no: int,
    ) -> None:
        node = self.get_node(db, node_id, tenant_key)
        if not node:
            return
        node.parent_id = parent_id
        node.name = name
        node.node_type = node_type
        node.content_text = content_text
        node.version_no = int(version_no)
        if hasattr(node, "updated_at"):
            node.updated_at = datetime.utcnow()

    def touch_node(self, db, *, node: Any) -> None:
        if hasattr(node, "updated_at"):
            node.updated_at = datetime.utcnow()

    def search_subtree_files(
        self,
        db,
        *,
        tenant_key: str,
        workspace_id: int,
        root_id: int,
        pattern: str,
        use_case_insensitive: bool,
        use_literal_case_sensitive: bool,
    ) -> list[dict[str, Any]]:
        nodes = {int(node.id): node for node in self.list_workspace_nodes(db, workspace_id, tenant_key)}
        results: list[dict[str, Any]] = []

        def matches(content_text: str) -> bool:
            if use_case_insensitive:
                return pattern.lower() in content_text.lower()
            if use_literal_case_sensitive:
                return pattern in content_text
            return True

        def visit(node_id: int, rel_path: str) -> None:
            current = nodes.get(int(node_id))
            if current is None:
                return
            if str(getattr(current, "node_type", "") or "") == "file":
                content_text = str(getattr(current, "content_text", "") or "")
                if matches(content_text):
                    results.append({"id": int(current.id), "rel_path": rel_path, "content_text": content_text})
            children = [
                node for node in nodes.values() if int(getattr(node, "parent_id", -1) or -1) == int(node_id)
            ]
            children.sort(key=lambda item: str(getattr(item, "name", "") or ""))
            for child in children:
                name = str(getattr(child, "name", "") or "")
                child_path = name if not rel_path else f"{rel_path}/{name}"
                visit(int(child.id), child_path)

        visit(int(root_id), "")
        results.sort(key=lambda item: str(item["rel_path"]))
        return results


@dataclass(frozen=True)
class InMemoryCommandLogRepository:
    state: _InMemoryRepoState

    def create_command_log(self, db, payload: dict[str, Any]) -> int:
        with self.state.lock:
            command_log_id = self.state.next_command_log_id
            self.state.next_command_log_id += 1
        self.state.command_logs[int(command_log_id)] = {"id": int(command_log_id), **dict(payload)}
        return int(command_log_id)

    def bulk_insert_command_logs(self, db, payloads: list[dict[str, Any]]) -> None:
        for payload in payloads:
            self.create_command_log(db, payload)


def build_in_memory_repositories(dependencies) -> VFSRepositories:
    state = _InMemoryRepoState()
    return VFSRepositories(
        workspace=InMemoryWorkspaceRepository(AgentWorkspace=dependencies.AgentWorkspace, state=state),
        session=InMemorySessionRepository(VirtualShellSession=dependencies.VirtualShellSession, state=state),
        node=InMemoryNodeRepository(VirtualFileNode=dependencies.VirtualFileNode, state=state),
        command_log=InMemoryCommandLogRepository(state=state),
    )
