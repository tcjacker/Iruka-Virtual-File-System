from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass


class VFSWorkspace(Base):
    __tablename__ = "vfs_workspaces"
    __table_args__ = (Index("idx_vfs_workspaces_tenant_id", "tenant_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    runtime_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    current_objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    focus_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VFSFileNode(Base):
    __tablename__ = "virtual_file_nodes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "parent_id", "name", name="uq_virtual_file_node_path"),
        Index("idx_virtual_file_nodes_tenant_workspace", "tenant_id", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    workspace_id: Mapped[int] = mapped_column(ForeignKey("vfs_workspaces.id"), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("virtual_file_nodes.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    node_type: Mapped[str] = mapped_column(String(16), nullable=False, default="file")
    content_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VFSShellSession(Base):
    __tablename__ = "virtual_shell_sessions"
    __table_args__ = (Index("idx_virtual_shell_sessions_tenant_workspace", "tenant_id", "workspace_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    workspace_id: Mapped[int] = mapped_column(ForeignKey("vfs_workspaces.id"), nullable=False)
    cwd_node_id: Mapped[int | None] = mapped_column(ForeignKey("virtual_file_nodes.id"), nullable=True)
    env_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VFSShellCommand(Base):
    __tablename__ = "virtual_shell_commands"
    __table_args__ = (Index("idx_virtual_shell_commands_tenant_session", "tenant_id", "session_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    session_id: Mapped[int] = mapped_column(ForeignKey("virtual_shell_sessions.id"), nullable=False)
    raw_cmd: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parsed_json: Mapped[dict] = mapped_column(JSON, default=dict)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stdout_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    artifacts_json: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VFSPatch(Base):
    __tablename__ = "virtual_patches"
    __table_args__ = (Index("idx_virtual_patches_tenant_workspace", "tenant_id", "workspace_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    workspace_id: Mapped[int] = mapped_column(ForeignKey("vfs_workspaces.id"), nullable=False)
    file_node_id: Mapped[int] = mapped_column(ForeignKey("virtual_file_nodes.id"), nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    patch_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="applied")
    conflict_json: Mapped[dict] = mapped_column(JSON, default=dict)
    applied_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# Backward-compatible aliases for host repos still expecting the old names.
AgentWorkspace = VFSWorkspace
VirtualFileNode = VFSFileNode
VirtualShellSession = VFSShellSession
VirtualShellCommand = VFSShellCommand
VirtualPatch = VFSPatch
