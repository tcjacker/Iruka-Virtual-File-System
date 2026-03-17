from __future__ import annotations

import argparse
import json
import shlex
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from iruka_vfs import WritableFileSource, create_workspace
from iruka_vfs.dependencies import VFSDependencies, configure_vfs_dependencies


class Base(DeclarativeBase):
    pass


class DemoWorkspace(Base):
    __tablename__ = "vfs_web_workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="demo")
    runtime_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    current_objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    focus_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DemoFileNode(Base):
    __tablename__ = "virtual_file_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="demo")
    workspace_id: Mapped[int] = mapped_column(ForeignKey("vfs_web_workspaces.id"), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("virtual_file_nodes.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    node_type: Mapped[str] = mapped_column(String(16), nullable=False, default="file")
    content_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DemoShellSession(Base):
    __tablename__ = "virtual_shell_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="demo")
    workspace_id: Mapped[int] = mapped_column(ForeignKey("vfs_web_workspaces.id"), nullable=False)
    cwd_node_id: Mapped[int | None] = mapped_column(ForeignKey("virtual_file_nodes.id"), nullable=True)
    env_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DemoShellCommand(Base):
    __tablename__ = "virtual_shell_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="demo")
    session_id: Mapped[int] = mapped_column(ForeignKey("virtual_shell_sessions.id"), nullable=False)
    raw_cmd: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parsed_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stdout_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artifacts_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DemoChapter:
    pass


@dataclass
class DemoSettings:
    default_tenant_id: str = "demo"
    redis_key_namespace: str = "iruka-vfs-web-demo"
    redis_url: str = "memory://"
    database_url: str = "sqlite+pysqlite:////tmp/iruka_vfs_web_demo.sqlite"


@dataclass
class InMemoryLock:
    _lock: threading.Lock

    def acquire(self, blocking: bool = True, blocking_timeout: float | None = None) -> bool:
        timeout = -1 if blocking_timeout is None else blocking_timeout
        return self._lock.acquire(blocking=blocking, timeout=timeout)

    def release(self) -> None:
        if self._lock.locked():
            self._lock.release()


@dataclass
class InMemoryRedis:
    store: dict[str, Any] = field(default_factory=dict)
    sets: dict[str, set[str]] = field(default_factory=dict)
    queues: dict[str, deque[str]] = field(default_factory=dict)
    locks: dict[str, threading.Lock] = field(default_factory=dict)
    condition: threading.Condition = field(default_factory=threading.Condition)

    def get(self, key: str) -> Any:
        return self.store.get(key)

    def set(self, key: str, value: Any) -> None:
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.store.pop(key, None)
        self.sets.pop(key, None)
        self.queues.pop(key, None)

    def sadd(self, key: str, value: str) -> int:
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.add(value)
        return 1 if len(bucket) != before else 0

    def srem(self, key: str, value: str) -> int:
        bucket = self.sets.setdefault(key, set())
        if value in bucket:
            bucket.remove(value)
            return 1
        return 0

    def scard(self, key: str) -> int:
        return len(self.sets.get(key, set()))

    def rpush(self, key: str, value: str) -> None:
        with self.condition:
            self.queues.setdefault(key, deque()).append(value)
            self.condition.notify_all()

    def blpop(self, key: str, timeout: int = 1) -> tuple[str, str] | None:
        deadline = time.time() + timeout
        with self.condition:
            while True:
                queue = self.queues.setdefault(key, deque())
                if queue:
                    return key, queue.popleft()
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self.condition.wait(timeout=remaining)

    def llen(self, key: str) -> int:
        return len(self.queues.get(key, deque()))

    def lock(self, key: str, timeout: int = 30, blocking_timeout: int = 5) -> InMemoryLock:
        return InMemoryLock(self.locks.setdefault(key, threading.Lock()))

    def clear(self) -> None:
        with self.condition:
            self.store.clear()
            self.sets.clear()
            self.queues.clear()
            self.locks.clear()
            self.condition.notify_all()


@dataclass
class MutableText:
    value: str
    lock: threading.Lock = field(default_factory=threading.Lock)

    def read(self) -> str:
        with self.lock:
            return self.value

    def write(self, text_value: str) -> None:
        with self.lock:
            self.value = text_value


class DemoApp:
    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self.html_path = REPO_ROOT / "examples" / "static" / "vfs_web_demo.html"
        self.lock = threading.RLock()
        self.session_local: sessionmaker
        self.workspace_row: DemoWorkspace | None = None
        self.workspace_handle: Any | None = None
        self.chapter_store: MutableText | None = None
        self.redis_client: InMemoryRedis | None = None
        self.tenant_id = "demo-ui"
        self.runtime_counter = 0
        self._configure_runtime()
        self.reset_workspace()

    def _configure_runtime(self) -> None:
        configure_vfs_dependencies(
            VFSDependencies(
                settings=DemoSettings(database_url=self.db_url),
                AgentWorkspace=DemoWorkspace,
                Chapter=DemoChapter,
                VirtualFileNode=DemoFileNode,
                VirtualShellCommand=DemoShellCommand,
                VirtualShellSession=DemoShellSession,
                load_project_state_payload=lambda *args, **kwargs: {},
            )
        )
        from iruka_vfs import service as vfs_service
        from iruka_vfs import workspace_mirror as vfs_workspace_mirror

        vfs_service.ASYNC_COMMAND_LOGGING = False
        vfs_service._ensure_workspace_checkpoint_worker_api = lambda engine: None
        vfs_workspace_mirror.enqueue_workspace_checkpoint = lambda base_key, **kwargs: None
        self.redis_client = InMemoryRedis()
        vfs_service._redis_client = self.redis_client
        engine_kwargs: dict[str, Any] = {"future": True}
        if self.db_url.startswith("sqlite"):
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_engine(self.db_url, **engine_kwargs)
        Base.metadata.create_all(bind=engine)
        self.session_local = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )

    def _clear_demo_rows(self) -> None:
        with self.session_local() as db:
            db.execute(delete(DemoShellCommand).where(DemoShellCommand.tenant_id == self.tenant_id))
            db.execute(delete(DemoShellSession).where(DemoShellSession.tenant_id == self.tenant_id))
            db.execute(delete(DemoFileNode).where(DemoFileNode.tenant_id == self.tenant_id))
            db.execute(delete(DemoWorkspace).where(DemoWorkspace.tenant_id == self.tenant_id))
            db.commit()

    def _clear_demo_runtime_state(self) -> None:
        from iruka_vfs import runtime_state
        from iruka_vfs import service as vfs_service
        from iruka_vfs import workspace_mirror as vfs_workspace_mirror

        if self.workspace_row is not None:
            try:
                with self.session_local() as db:
                    vfs_workspace_mirror.delete_workspace_mirror(
                        int(self.workspace_row.id),
                        tenant_id=self.tenant_id,
                        scope_key=vfs_workspace_mirror.workspace_scope_for_db(db),
                    )
            except Exception:
                pass
        if self.redis_client is not None:
            self.redis_client.clear()
        with runtime_state.runtime_seed_lock:
            runtime_state.runtime_seeds = {
                key: value
                for key, value in runtime_state.runtime_seeds.items()
                if str(key[0]) != self.tenant_id
            }
        with vfs_service._workspace_cache_lock:
            vfs_service._workspace_cache.clear()
        vfs_workspace_mirror.set_active_workspace_mirror(None)
        vfs_workspace_mirror.set_active_workspace_tenant(None)
        vfs_workspace_mirror.set_active_workspace_scope(None)

    def reset_workspace(self) -> dict[str, Any]:
        with self.lock:
            self._clear_demo_runtime_state()
            self._clear_demo_rows()
            self.runtime_counter += 1
            self.chapter_store = MutableText(
                "Scene opening.\nThe editor is live.\nMARKER_000\nMARKER_001\n"
            )
            with self.session_local() as db:
                workspace_row = DemoWorkspace(
                    tenant_id=self.tenant_id,
                    runtime_key=f"web-demo:{self.runtime_counter}",
                    project_id=1,
                    status="idle",
                    current_objective="interactive verification",
                )
                db.add(workspace_row)
                db.commit()
                db.refresh(workspace_row)
                self.workspace_row = workspace_row
                self.workspace_handle = create_workspace(
                    workspace=workspace_row,
                    tenant_id=self.tenant_id,
                    runtime_key=f"web-demo:{workspace_row.id}:{self.runtime_counter}",
                    primary_file=WritableFileSource(
                        file_id="chapter:1",
                        virtual_path="/workspace/chapters/chapter_1.md",
                        read_text=self.chapter_store.read,
                        write_text=self.chapter_store.write,
                        metadata={"source_type": "web-demo"},
                    ),
                    context_files={
                        "outline.md": "# Outline\n\n- Verify bash edits\n- Inspect tree\n",
                        "notes.md": "Use /workspace/notes for scratch files.\n",
                    },
                    skill_files={
                        "style.md": "# Style\n\nKeep edits local and deterministic.\n",
                    },
                )
                self.workspace_handle.ensure(db, include_tree=False)
                self.workspace_handle.enter_agent_mode(db)
            return self.get_state()

    def run_command(self, command: str) -> dict[str, Any]:
        if not command.strip():
            raise ValueError("command is required")
        if self.workspace_handle is None:
            raise RuntimeError("workspace is not initialized")
        with self.lock:
            with self.session_local() as db:
                started = time.perf_counter()
                result = self._run_demo_command(db, command)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
            payload = self.get_state()
            payload["result"] = {
                "command": command,
                "elapsed_ms": elapsed_ms,
                **result,
            }
            return payload

    def _run_demo_command(self, db: Session, command: str) -> dict[str, Any]:
        tokens = shlex.split(command)
        if not tokens:
            raise ValueError("command is required")
        if tokens[0] == "rm":
            result = self._run_demo_rm(db, command, tokens)
            self._record_demo_command(db, command, result)
            return result
        if tokens[0] == "mv":
            result = self._run_demo_mv(db, command, tokens)
            self._record_demo_command(db, command, result)
            return result
        return self.workspace_handle.bash(db, command)

    def _run_demo_rm(self, db: Session, command: str, tokens: list[str]) -> dict[str, Any]:
        from iruka_vfs import service as vfs_service

        recursive = False
        force = False
        targets: list[str] = []
        for token in tokens[1:]:
            if token in {"-r", "-R"}:
                recursive = True
                continue
            if token == "-f":
                force = True
                continue
            if token in {"-rf", "-fr"}:
                recursive = True
                force = True
                continue
            targets.append(token)
        if not targets:
            return self._demo_result(command, "", "rm: missing operand", 1, "/workspace")

        mirror = vfs_service._get_workspace_mirror_api(self.workspace_row.id, 1, tenant_key=self.tenant_id)
        if not mirror:
            raise RuntimeError("workspace mirror missing")

        removed: list[str] = []
        session = vfs_service._get_or_create_session(db, self.workspace_row.id)
        with mirror.lock:
            cwd_path = self._mirror_node_path(mirror, int(mirror.cwd_node_id))
            for raw_path in targets:
                normalized_path = vfs_service._normalize_virtual_path(db, session, raw_path)
                if not normalized_path or normalized_path == "/":
                    return self._demo_result(command, "", f"rm: cannot remove '{raw_path}'", 1, cwd_path)
                node_id = mirror.path_to_id.get(normalized_path)
                node = mirror.nodes.get(int(node_id)) if node_id is not None else None
                if not node:
                    if force:
                        continue
                    return self._demo_result(command, "", f"rm: cannot remove '{raw_path}': No such file or directory", 1, cwd_path)
                if int(node.id) == int(mirror.root_id):
                    return self._demo_result(command, "", "rm: refusing to remove root directory", 1, cwd_path)
                if node.node_type == "dir" and not recursive:
                    return self._demo_result(command, "", f"rm: cannot remove '{raw_path}': Is a directory", 1, cwd_path)
                removed.append(normalized_path)
                self._remove_subtree_from_mirror(mirror, int(node.id))
            if removed:
                mirror.revision += 1
                vfs_service._set_workspace_mirror_api(mirror)
            cwd_path = self._mirror_node_path(mirror, int(mirror.cwd_node_id))
        return self._demo_result(command, "\n".join(removed), "", 0, cwd_path)

    def _run_demo_mv(self, db: Session, command: str, tokens: list[str]) -> dict[str, Any]:
        from iruka_vfs import service as vfs_service

        if len(tokens) != 3:
            return self._demo_result(command, "", "mv: require source and destination", 1, "/workspace")
        source_path, dest_path = tokens[1], tokens[2]
        mirror = vfs_service._get_workspace_mirror_api(self.workspace_row.id, 1, tenant_key=self.tenant_id)
        if not mirror:
            raise RuntimeError("workspace mirror missing")

        session = vfs_service._get_or_create_session(db, self.workspace_row.id)
        with mirror.lock:
            cwd_path = self._mirror_node_path(mirror, int(mirror.cwd_node_id))
            normalized_source = vfs_service._normalize_virtual_path(db, session, source_path)
            if not normalized_source or normalized_source == "/":
                return self._demo_result(command, "", f"mv: cannot stat '{source_path}': No such file or directory", 1, cwd_path)
            source_id = mirror.path_to_id.get(normalized_source)
            source = mirror.nodes.get(int(source_id)) if source_id is not None else None
            if not source:
                return self._demo_result(command, "", f"mv: cannot stat '{source_path}': No such file or directory", 1, cwd_path)
            normalized_dest = vfs_service._normalize_virtual_path(db, vfs_service._get_or_create_session(db, self.workspace_row.id), dest_path)
            if not normalized_dest or normalized_dest == "/":
                return self._demo_result(command, "", f"mv: invalid destination '{dest_path}'", 1, cwd_path)
            existing_id = mirror.path_to_id.get(normalized_dest)
            existing = mirror.nodes.get(int(existing_id)) if existing_id is not None else None
            target_path = normalized_dest
            if existing is not None:
                if existing.node_type != "dir":
                    return self._demo_result(command, "", f"mv: destination exists: {normalized_dest}", 1, cwd_path)
                leaf = str(source.name or "")
                target_path = normalized_dest.rstrip("/") + "/" + leaf if normalized_dest != "/" else "/" + leaf
                if mirror.path_to_id.get(target_path) is not None:
                    return self._demo_result(command, "", f"mv: destination exists: {target_path}", 1, cwd_path)
            parent_path, _, leaf = target_path.rpartition("/")
            parent_path = parent_path or "/"
            parent = mirror.nodes.get(mirror.path_to_id.get(parent_path, -10))
            if not parent or parent.node_type != "dir":
                return self._demo_result(command, "", f"mv: invalid destination parent: {parent_path}", 1, cwd_path)
            node = mirror.nodes[int(source.id)]
            source_full_path = self._mirror_node_path(mirror, int(node.id))
            if node.parent_id == int(parent.id) and node.name == leaf:
                return self._demo_result(command, target_path, "", 0, cwd_path)
            subtree_paths = {
                path
                for path, node_id in mirror.path_to_id.items()
                if int(node_id) == int(node.id) or path.startswith(source_full_path.rstrip("/") + "/")
            }
            if parent_path in subtree_paths:
                return self._demo_result(command, "", "mv: cannot move a directory into itself", 1, cwd_path)
            old_parent_id = node.parent_id
            node.parent_id = int(parent.id)
            node.name = leaf
            if old_parent_id is not None and old_parent_id in mirror.children_by_parent:
                mirror.children_by_parent[old_parent_id] = [child_id for child_id in mirror.children_by_parent[old_parent_id] if child_id != int(node.id)]
            mirror.children_by_parent.setdefault(int(parent.id), []).append(int(node.id))
            vfs_service._ensure_children_sorted_locked(mirror, int(parent.id))
            vfs_service._rebuild_workspace_mirror_indexes_locked(mirror)
            mirror.dirty_structure_node_ids.add(int(node.id))
            mirror.revision += 1
            vfs_service._set_workspace_mirror_api(mirror)
            cwd_path = self._mirror_node_path(mirror, int(mirror.cwd_node_id))
        return self._demo_result(command, target_path, "", 0, cwd_path)

    def _remove_subtree_from_mirror(self, mirror: Any, node_id: int) -> None:
        stack = [int(node_id)]
        removed_ids: list[int] = []
        while stack:
            current_id = stack.pop()
            stack.extend(list(mirror.children_by_parent.get(current_id, [])))
            removed_ids.append(current_id)
        for current_id in sorted(removed_ids, reverse=True):
            node = mirror.nodes.pop(current_id, None)
            if not node:
                continue
            mirror.children_by_parent.pop(current_id, None)
            if node.parent_id is not None and node.parent_id in mirror.children_by_parent:
                mirror.children_by_parent[node.parent_id] = [child_id for child_id in mirror.children_by_parent[node.parent_id] if child_id != current_id]
            mirror.dirty_content_node_ids.discard(current_id)
            mirror.dirty_structure_node_ids.discard(current_id)
            if int(current_id) == int(mirror.cwd_node_id):
                workspace_id = mirror.path_to_id.get("/workspace")
                mirror.cwd_node_id = int(workspace_id or mirror.root_id)
        from iruka_vfs import service as vfs_service

        vfs_service._rebuild_workspace_mirror_indexes_locked(mirror)

    def _mirror_node_path(self, mirror: Any, node_id: int) -> str:
        from iruka_vfs import service as vfs_service

        node = mirror.nodes.get(int(node_id))
        if not node:
            return "/"
        return vfs_service._mirror_node_path_locked(mirror, node)

    def _demo_result(self, command: str, stdout: str, stderr: str, exit_code: int, cwd: str) -> dict[str, Any]:
        return {
            "session_id": 1,
            "command_id": 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "artifacts": {"results": [{"cmd": command, "exit_code": exit_code, "artifacts": {}}]},
            "cwd": cwd,
        }

    def _record_demo_command(self, db: Session, command: str, result: dict[str, Any]) -> None:
        row = DemoShellCommand(
            tenant_id=self.tenant_id,
            session_id=int(result.get("session_id") or 1),
            raw_cmd=command,
            parsed_json={"segments": [command]},
            exit_code=int(result.get("exit_code") or 0),
            stdout_text=str(result.get("stdout") or ""),
            stderr_text=str(result.get("stderr") or ""),
            artifacts_json=dict(result.get("artifacts") or {}),
            started_at=datetime.utcnow(),
            ended_at=datetime.utcnow(),
        )
        db.add(row)
        db.commit()
        result["command_id"] = int(row.id or 0)

    def flush(self) -> dict[str, Any]:
        if self.workspace_handle is None:
            raise RuntimeError("workspace is not initialized")
        with self.lock:
            started = time.perf_counter()
            ok = bool(self.workspace_handle.flush())
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            payload = self.get_state()
            payload["flush"] = {"ok": ok, "elapsed_ms": elapsed_ms}
            return payload

    def get_state(self) -> dict[str, Any]:
        from iruka_vfs.memory_cache import snapshot_virtual_fs_cache_metrics
        from iruka_vfs.workspace_mirror import snapshot_workspace_checkpoint_metrics

        if self.workspace_handle is None or self.workspace_row is None or self.chapter_store is None:
            raise RuntimeError("workspace is not initialized")
        with self.lock:
            with self.session_local() as db:
                snapshot = self.workspace_handle.ensure(db, include_tree=True)
                recent_commands = list(
                    db.scalars(
                        select(DemoShellCommand)
                        .where(DemoShellCommand.tenant_id == self.tenant_id)
                        .order_by(DemoShellCommand.id.desc())
                        .limit(12)
                    ).all()
                )
            recent_payload = [
                {
                    "id": int(item.id),
                    "raw_cmd": item.raw_cmd,
                    "exit_code": int(item.exit_code),
                    "ended_at": item.ended_at.isoformat() if item.ended_at else "",
                }
                for item in recent_commands
            ]
            return {
                "workspace_id": int(self.workspace_row.id),
                "runtime_key": str(self.workspace_row.runtime_key),
                "chapter_file": str(snapshot.get("chapter_file") or ""),
                "tree": str(snapshot.get("tree") or ""),
                "host_text": self.chapter_store.read(),
                "cache_metrics": snapshot_virtual_fs_cache_metrics(),
                "checkpoint_metrics": snapshot_workspace_checkpoint_metrics(),
                "recent_commands": recent_payload,
            }


class DemoRequestHandler(BaseHTTPRequestHandler):
    server_version = "IrukaVFSDemo/0.1"

    @property
    def app(self) -> DemoApp:
        return self.server.app  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(self.app.html_path.read_text(encoding="utf-8"))
            return
        if path == "/api/state":
            self._send_json(self.app.get_state())
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/command":
                self._send_json(self.app.run_command(str(body.get("command") or "")))
                return
            if path == "/api/flush":
                self._send_json(self.app.flush())
                return
            if path == "/api/reset":
                self._send_json(self.app.reset_workspace())
                return
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": type(exc).__name__, "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        return dict(json.loads(raw.decode("utf-8")))

    def _send_html(self, html: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local web UI for interactive iruka_vfs verification.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--db-url",
        default="sqlite+pysqlite:////tmp/iruka_vfs_web_demo.sqlite",
        help="SQLAlchemy database URL. Defaults to a local SQLite file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = DemoApp(db_url=args.db_url)
    server = ThreadingHTTPServer((args.host, args.port), DemoRequestHandler)
    server.app = app  # type: ignore[attr-defined]
    print(f"Serving iruka_vfs demo at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
