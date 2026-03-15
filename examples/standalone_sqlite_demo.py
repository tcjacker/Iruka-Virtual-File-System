from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import sys
import threading
import time
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from iruka_vfs import WritableFileSource, create_workspace
from iruka_vfs.dependencies import VFSDependencies, configure_vfs_dependencies


class Base(DeclarativeBase):
    pass


class DemoWorkspace(Base):
    __tablename__ = "vfs_workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="demo")
    runtime_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    chapter_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    current_objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    focus_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DemoFileNode(Base):
    __tablename__ = "virtual_file_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="demo")
    workspace_id: Mapped[int] = mapped_column(ForeignKey("vfs_workspaces.id"), nullable=False)
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
    workspace_id: Mapped[int] = mapped_column(ForeignKey("vfs_workspaces.id"), nullable=False)
    cwd_node_id: Mapped[int | None] = mapped_column(ForeignKey("virtual_file_nodes.id"), nullable=True)
    env_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DemoShellCommand(Base):
    __tablename__ = "virtual_shell_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="demo")
    session_id: Mapped[int] = mapped_column(ForeignKey("virtual_shell_sessions.id"), nullable=False)
    raw_cmd: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parsed_json: Mapped[dict] = mapped_column(JSON, default=dict)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stdout_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artifacts_json: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DemoChapter:
    pass


@dataclass
class DemoSettings:
    default_tenant_id: str = "demo"
    redis_key_namespace: str = "iruka-vfs-demo"
    redis_url: str = "memory://"
    database_url: str = "sqlite+pysqlite:///:memory:"


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

    def lock(self, key: str, timeout: int = 30, blocking_timeout: int = 5) -> InMemoryLock:
        return InMemoryLock(self.locks.setdefault(key, threading.Lock()))


def main() -> None:
    configure_vfs_dependencies(
        VFSDependencies(
            settings=DemoSettings(),
            AgentWorkspace=DemoWorkspace,
            Chapter=DemoChapter,
            VirtualFileNode=DemoFileNode,
            VirtualShellCommand=DemoShellCommand,
            VirtualShellSession=DemoShellSession,
            load_project_state_payload=lambda *args, **kwargs: {},
        )
    )

    from iruka_vfs import service as vfs_service

    vfs_service._redis_client = InMemoryRedis()

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)

    chapter_text = {"value": "First draft line.\nSecond line.\n"}

    with SessionLocal() as db:
        workspace_row = DemoWorkspace(
            tenant_id="demo",
            runtime_key="workspace:1",
            project_id=1,
            chapter_id=1,
            status="idle",
        )
        db.add(workspace_row)
        db.commit()
        db.refresh(workspace_row)

        workspace = create_workspace(
            workspace=workspace_row,
            tenant_id="demo",
            runtime_key=f"workspace:{workspace_row.id}",
            chapter_id=1,
            primary_file=WritableFileSource(
                file_id="chapter:1",
                virtual_path="/workspace/chapters/chapter_1.md",
                read_text=lambda: chapter_text["value"],
                write_text=lambda text: chapter_text.__setitem__("value", text),
                metadata={"source_type": "standalone-demo"},
            ),
            context_files={"outline.md": "# Outline\n\nA small standalone demo.\n"},
            skill_files={"index.md": "# Skills\n\n- none\n"},
        )

        snapshot = workspace.ensure(db)
        print("tree:\n", snapshot.get("tree") or "")

        read_result = workspace.bash(db, "cat /workspace/chapters/chapter_1.md")
        print("cat stdout:\n", read_result["stdout"])

        edit_result = workspace.bash(
            db,
            "edit /workspace/chapters/chapter_1.md --find First --replace Rewritten",
        )
        print("edit exit_code:", edit_result["exit_code"])
        print("edit stdout:\n", edit_result["stdout"])

        workspace.flush()
        print("host text after flush:\n", chapter_text["value"])


if __name__ == "__main__":
    main()
