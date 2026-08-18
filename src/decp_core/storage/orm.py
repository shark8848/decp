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

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, Index
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


class TaskOrm(Base):
    """团队任务（task 数据域）：研发/项目/技术债/运营/事务，看板排期与跟踪。"""

    __tablename__ = "task"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, default="default")
    type: Mapped[str] = mapped_column(String, nullable=False, default="requirement")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    module: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="backlog")
    priority: Mapped[str] = mapped_column(String, nullable=False, default="P2")
    assignee: Mapped[str | None] = mapped_column(String, nullable=True)
    sprint_id: Mapped[str | None] = mapped_column(String, nullable=True)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plan_links: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    requirement_id: Mapped[str | None] = mapped_column(String, nullable=True)
    feedback_ids: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    bug_ids: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    source_refs: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    labels: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    extra: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("idx_task_workspace_status", "workspace_id", "status"),
        Index("idx_task_type", "type"),
        Index("idx_task_sprint", "sprint_id"),
        Index("idx_task_assignee", "assignee"),
        Index("idx_task_requirement", "requirement_id"),
    )


class BugOrm(Base):
    """缺陷（bug 数据域）：独立全生命周期，与反馈/需求/任务/会议多域关联。"""

    __tablename__ = "bug"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, default="default")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    module: Mapped[str | None] = mapped_column(String, nullable=True)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="medium")
    priority: Mapped[str] = mapped_column(String, nullable=False, default="P2")
    status: Mapped[str] = mapped_column(String, nullable=False, default="new")
    channel: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    environment: Mapped[str | None] = mapped_column(String, nullable=True)
    reproduce_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee: Mapped[str | None] = mapped_column(String, nullable=True)
    reporter: Mapped[str] = mapped_column(String, nullable=False, default="maintainer")
    sprint_id: Mapped[str | None] = mapped_column(String, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fix_version: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_links: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    feedback_ids: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    requirement_ids: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    task_ids: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    meeting_ids: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    source_refs: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    labels: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    extra: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fixed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("idx_bug_workspace_status", "workspace_id", "status"),
        Index("idx_bug_severity", "severity"),
        Index("idx_bug_assignee", "assignee"),
        Index("idx_bug_channel", "channel"),
    )


class SprintOrm(Base):
    """迭代排期（sprint 数据域）。"""

    __tablename__ = "sprint"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, default="default")
    name: Mapped[str] = mapped_column(String, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="planned")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("idx_sprint_workspace", "workspace_id"),)


class TaskLogOrm(Base):
    """任务/缺陷活动流（审计留痕）。"""

    __tablename__ = "task_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, default="default")
    task_id: Mapped[str] = mapped_column(String, nullable=False)
    entity: Mapped[str] = mapped_column(String, nullable=False, default="task")
    action: Mapped[str] = mapped_column(String, nullable=False, default="created")
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str | None] = mapped_column(String, nullable=True)
    field: Mapped[str | None] = mapped_column(String, nullable=True)
    old_value: Mapped[Any] = mapped_column(JsonType, nullable=True)
    new_value: Mapped[Any] = mapped_column(JsonType, nullable=True)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_task_log_task", "entity", "task_id"),
        Index("idx_task_log_workspace", "workspace_id"),
    )


class MeetingMinutesOrm(Base):
    """会议纪要（meeting_minutes 数据域）。"""

    __tablename__ = "meeting_minutes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, default="default")
    title: Mapped[str] = mapped_column(String, nullable=False)
    held_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    participants: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String, nullable=True)
    agenda: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    module: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decisions: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    action_items: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    keywords: Mapped[list] = mapped_column(JsonType, nullable=False, default=list)
    submitted_by: Mapped[str] = mapped_column(String, nullable=False, default="maintainer")
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("idx_meeting_workspace", "workspace_id"),
        Index("idx_meeting_held_at", "held_at"),
    )


class AttachmentOrm(Base):
    """通用附件/链接登记（attachment 数据域）：方案上传自动管理。"""

    __tablename__ = "attachment"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, default="default")
    entity: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    mime: Mapped[str | None] = mapped_column(String, nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uploaded_by: Mapped[str] = mapped_column(String, nullable=False, default="maintainer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_attachment_entity", "entity", "entity_id"),
        Index("idx_attachment_workspace", "workspace_id"),
    )
