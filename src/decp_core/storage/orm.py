# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""SQLAlchemy 2.0 ORM 模型定义（SQLite / PostgreSQL 双后端共用）。

- 单一模型定义，通过 engine URL 切换方言（sqlite+aiosqlite:// 或 postgresql+psycopg://）
- JSON 列在 SQLite 落地为 TEXT、PostgreSQL 落地为 JSONB
- 字段与 `decp_core.models` 完全对齐；schema 与旧手写 SQL 兼容（create_all 幂等）
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, Integer, String, Text, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """声明基类。"""


# JSON 列类型：SQLite 用 TEXT 语义、PG 用 JSONB（更利于索引/查询）
JsonType = JSON().with_variant(JSONB, "postgresql")


class FeedbackOrm(Base):
    """客户反馈（feedback 数据域）。"""

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, default="default")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False, default="natural_language")
    customer: Mapped[str | None] = mapped_column(String, nullable=True)
    module: Mapped[str | None] = mapped_column(String, nullable=True)
    feedback_type: Mapped[str | None] = mapped_column(String, nullable=True)
    impact: Mapped[str | None] = mapped_column(String, nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    submitted_by: Mapped[str] = mapped_column(String, nullable=False, default="maintainer")
    structured: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_feedback_customer", "customer"),
        Index("idx_feedback_module", "module"),
        Index("idx_feedback_created_at", "created_at"),
        Index("idx_feedback_workspace", "workspace_id"),
    )


class RequirementOrm(Base):
    """产品需求（requirement 数据域，Product Workspace 正式对象）。"""

    __tablename__ = "requirement"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, default="default")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    module: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[str] = mapped_column(String, nullable=False, default="P2")
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    feedback_ids: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    source_refs: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    cluster_id: Mapped[str | None] = mapped_column(String, nullable=True)
    impact_customers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    similar_feedback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tags: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    extra: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("idx_requirement_status", "status"),
        Index("idx_requirement_priority", "priority"),
        Index("idx_requirement_module", "module"),
        Index("idx_requirement_updated_at", "updated_at"),
        Index("idx_requirement_archived", "archived"),
        Index("idx_requirement_workspace", "workspace_id"),
    )


class UserOrm(Base):
    """平台用户（轻量注册，首次出现自动建档）。"""

    __tablename__ = "user"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceOrm(Base):
    """产品工作区：一个产品一个 workspace，数据按 workspace 隔离。"""

    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    passcode: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("idx_workspace_owner", "owner_user_id"),)


class WorkspaceMemberOrm(Base):
    """工作区成员：owner 可审批加入，member 只读写。"""

    __tablename__ = "workspace_member"

    workspace_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="member")
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_member_user", "user_id"),
        Index("idx_member_status", "status"),
    )


class AppMetaOrm(Base):
    """应用元数据（版本/hash 审计）。"""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JsonType, nullable=True)
