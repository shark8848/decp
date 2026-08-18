# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""领域模型：feedback（反馈）与 requirement（需求）两大数据域。"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------

Priority = Literal["P0", "P1", "P2", "P3"]
ReqStatus = Literal["draft", "reviewing", "accepted", "merged", "rejected"]
FeedbackChannel = Literal["natural_language", "excel", "ticket", "api"]
MemberRole = Literal["owner", "member"]
JoinStatus = Literal["pending", "approved", "rejected"]

# 默认工作区 / 默认用户：存量数据兜底，未显式指定身份时的归属
DEFAULT_WORKSPACE_ID = "default"
DEFAULT_USER_ID = "default_user"


class SourceRef(BaseModel):
    """来源追踪：需求/反馈的可追溯引用。"""

    model_config = ConfigDict(extra="ignore")

    ref_type: Literal["feedback", "ticket", "excel", "api", "manual",
                      "meeting", "sprint", "bug", "requirement"]
    ref_id: str
    detail: str | None = None


# ---------------------------------------------------------------------------
# 用户 / 工作区（多租户隔离）
# ---------------------------------------------------------------------------

class User(BaseModel):
    """平台用户：数字员工调用方的身份标识（轻量注册，首次出现自动建档）。"""

    model_config = ConfigDict(extra="ignore")

    user_id: str = Field(min_length=1, description="用户唯一标识")
    name: str | None = None
    created_at: datetime


class Workspace(BaseModel):
    """产品工作区：一个产品对应一个 workspace，数据按 workspace 隔离。

    passcode 为工作区通行证：创建时自动生成，持有者可凭通行证直接加入
    （绕过身份绑定的 owner 审批）。属主不可变——通行证是"凭证式授权"，
    不绑定调用者身份。非 owner 查询时由 service 层脱敏。
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="工作区 id，如 ws-xxx")
    name: str = Field(min_length=1, description="工作区名称（产品名）")
    owner_user_id: str = Field(min_length=1, description="创建者（owner）")
    description: str = ""
    passcode: str | None = Field(default=None, description="工作区通行证（仅 owner 可见）")
    created_at: datetime


class WorkspaceMember(BaseModel):
    """工作区成员：owner 可审批加入，member 只读写。"""

    model_config = ConfigDict(extra="ignore")

    workspace_id: str
    user_id: str
    role: MemberRole = "member"
    status: JoinStatus = "pending"
    joined_at: datetime | None = None


# ---------------------------------------------------------------------------
# Feedback 数据域
# ---------------------------------------------------------------------------

class FeedbackCreate(BaseModel):
    """创建反馈（入口数据）。"""

    model_config = ConfigDict(extra="ignore")

    content: str = Field(min_length=1, description="反馈原文（自然语言/工单描述等）")
    channel: FeedbackChannel = "natural_language"
    customer: str | None = None
    module: str | None = None
    feedback_type: str | None = None
    impact: str | None = None
    source_ref: str | None = Field(default=None, description="外部来源标识，如工单号")
    submitted_by: str = "maintainer"


class Feedback(FeedbackCreate):
    """反馈记录（feedback 数据域）。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")
    created_at: datetime
    structured: dict = Field(default_factory=dict, description="结构化抽取结果")


# ---------------------------------------------------------------------------
# Requirement 数据域
# ---------------------------------------------------------------------------

class RequirementCreate(BaseModel):
    """创建需求草稿。"""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, description="需求标题")
    description: str = ""
    module: str | None = None
    priority: Priority = "P2"
    status: ReqStatus = "draft"
    feedback_ids: list[str] = Field(default_factory=list, description="关联的反馈 id")
    source_refs: list[SourceRef] = Field(default_factory=list, description="来源引用（可追溯）")
    cluster_id: str | None = None
    impact_customers: int = 0
    similar_feedback_count: int = 0
    confidence: float = 0.0
    tags: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict, description="扩展分析字段（聚类/影响分析等）")


class Requirement(RequirementCreate):
    """需求对象（requirement 数据域，Product Workspace 正式对象）。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")
    version: int = 1
    created_at: datetime
    updated_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None
    archived: bool = False
    archived_at: datetime | None = None
    archived_by: str | None = None


# ---------------------------------------------------------------------------
# 分析输出
# ---------------------------------------------------------------------------

class AnalysisResult(BaseModel):
    """AI 整理与分析产出：分类、去重、聚类、影响分析、优先级建议、来源校验。"""

    model_config = ConfigDict(extra="ignore")

    categories: dict[str, list[str]] = Field(default_factory=dict, description="分类结果：category -> feedback_ids")
    duplicate_groups: list[list[str]] = Field(default_factory=list, description="去重：相似反馈 id 分组")
    clusters: list[dict] = Field(default_factory=list, description="聚类：{id, title, feedback_ids, keywords, score}")
    priorities: dict[str, str] = Field(default_factory=dict, description="优先级建议：feedback_id -> P0/P1/P2/P3")
    impact: dict[str, dict] = Field(default_factory=dict, description="影响分析：feedback_id -> {customers, module, severity}")
    sources_verified: list[dict] = Field(default_factory=list, description="来源校验结果")


class RequirementDraft(BaseModel):
    """需求草稿（产品经理可审核的对象）。"""

    model_config = ConfigDict(extra="ignore")

    req: Requirement
    analysis: AnalysisResult


# ---------------------------------------------------------------------------
# 团队任务 / 缺陷 / 会议纪要数据域（v2 扩展）
# ---------------------------------------------------------------------------

# ---- 枚举 ----
TaskType = Literal["requirement", "project", "tech_debt", "ops", "chore"]
TaskStatus = Literal["backlog", "todo", "in_progress", "review", "blocked",
                     "done", "cancelled"]
SprintStatus = Literal["planned", "active", "closed"]
MeetingItemKind = Literal["dev", "chore"]
BugSeverity = Literal["critical", "high", "medium", "low"]
BugStatus = Literal["new", "confirmed", "in_progress", "fixed", "verified",
                    "closed", "wonfix"]
BugChannel = Literal["feedback", "meeting", "manual", "qa", "monitor", "api"]


class ActionItem(BaseModel):
    """会议待办：强类型条目，供批量任务化。"""

    model_config = ConfigDict(extra="ignore")

    desc: str = Field(min_length=1, description="待办描述")
    owner: str | None = None
    due: date | None = None
    kind: MeetingItemKind = "chore"
    note: str | None = None


class Task(BaseModel):
    """团队任务：研发需求 / 项目 / 技术债 / 运营 / 事务任务，看板排期与跟踪。

    bug 走独立数据域（Bug），任务经 bug_ids / source_refs 关联缺陷。
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")
    type: TaskType = "requirement"
    title: str = Field(min_length=1, description="任务标题")
    description: str = ""
    module: str | None = None
    status: TaskStatus = "backlog"
    priority: Priority = "P2"
    assignee: str | None = Field(default=None, description="责任人（须为 workspace 成员）")
    sprint_id: str | None = Field(default=None, description="排期迭代")
    planned_start: datetime | None = None
    due_at: datetime | None = None
    estimate: float | None = None
    order: int = 0
    plan_links: list[str] = Field(default_factory=list, description="方案链接（上传自动管理）")
    requirement_id: str | None = None
    feedback_ids: list[str] = Field(default_factory=list)
    bug_ids: list[str] = Field(default_factory=list, description="关联缺陷（bug 独立域）")
    source_refs: list[SourceRef] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    done_at: datetime | None = None
    archived: bool = False
    archived_at: datetime | None = None
    archived_by: str | None = None


class TaskCreate(BaseModel):
    """创建任务（入口数据）。"""

    model_config = ConfigDict(extra="ignore")

    type: TaskType = "requirement"
    title: str = Field(min_length=1, description="任务标题")
    description: str = ""
    module: str | None = None
    priority: Priority = "P2"
    assignee: str | None = None
    sprint_id: str | None = None
    planned_start: datetime | None = None
    due_at: datetime | None = None
    estimate: float | None = None
    plan_links: list[str] = Field(default_factory=list)
    requirement_id: str | None = None
    feedback_ids: list[str] = Field(default_factory=list)
    bug_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)
    submitted_by: str = "maintainer"


class Bug(BaseModel):
    """缺陷：独立全生命周期管理，与反馈/需求/任务/会议多域关联。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")
    title: str = Field(min_length=1, description="缺陷标题")
    description: str = ""
    module: str | None = None
    severity: BugSeverity = "medium"
    priority: Priority = "P2"
    status: BugStatus = "new"
    channel: BugChannel = "manual"
    environment: str | None = None
    reproduce_steps: str | None = None
    expected: str | None = None
    actual: str | None = None
    assignee: str | None = Field(default=None, description="处理人（须为 workspace 成员）")
    reporter: str = "maintainer"
    sprint_id: str | None = None
    due_at: datetime | None = None
    fix_version: str | None = None
    plan_links: list[str] = Field(default_factory=list)
    feedback_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    meeting_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    fixed_at: datetime | None = None
    closed_at: datetime | None = None
    archived: bool = False
    archived_at: datetime | None = None
    archived_by: str | None = None


class BugCreate(BaseModel):
    """创建缺陷（入口数据）。"""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, description="缺陷标题")
    description: str = ""
    module: str | None = None
    severity: BugSeverity = "medium"
    priority: Priority = "P2"
    channel: BugChannel = "manual"
    environment: str | None = None
    reproduce_steps: str | None = None
    expected: str | None = None
    actual: str | None = None
    assignee: str | None = None
    reporter: str = "maintainer"
    sprint_id: str | None = None
    due_at: datetime | None = None
    fix_version: str | None = None
    feedback_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    meeting_ids: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)
    submitted_by: str = "maintainer"


class Sprint(BaseModel):
    """迭代排期：一组任务的排期容器，按时间轴跟踪。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")
    name: str = Field(min_length=1, description="迭代名，如 Sprint 24-08")
    goal: str = ""
    start_date: datetime
    end_date: datetime
    status: SprintStatus = "planned"
    created_at: datetime


class SprintCreate(BaseModel):
    """创建迭代（入口数据）。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, description="迭代名，如 Sprint 24-08")
    goal: str = ""
    start_date: datetime
    end_date: datetime
    status: SprintStatus = "planned"
    submitted_by: str = "maintainer"


class TaskLog(BaseModel):
    """任务/缺陷活动流：状态流转/指派/排期变更/方案上传留痕。"""

    model_config = ConfigDict(extra="ignore")

    id: int
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")
    task_id: str
    entity: str = "task"  # task | bug
    action: str = "created"
    from_status: str | None = None
    to_status: str | None = None
    field: str | None = None
    old_value: dict | list | str | int | float | bool | None = None
    new_value: dict | list | str | int | float | bool | None = None
    actor: str
    comment: str | None = None
    created_at: datetime


class MeetingMinutes(BaseModel):
    """会议纪要：原文存档 + 启发式提取的摘要/决议/待办，结构化沉淀。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")
    title: str = Field(min_length=1, description="会议主题")
    held_at: datetime = Field(default_factory=utcnow, description="会议时间（默认提交时间）")
    participants: list[str] = Field(default_factory=list)
    location: str | None = None
    recording_url: str | None = None
    agenda: list[str] = Field(default_factory=list)
    module: str | None = None
    raw_text: str = Field(min_length=1, description="原始纪要全文（存档原文，不可丢失）")
    summary: str = ""
    decisions: list[dict] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    submitted_by: str = "maintainer"
    source_ref: str | None = None
    created_at: datetime
    updated_at: datetime
    archived: bool = False
    archived_at: datetime | None = None
    archived_by: str | None = None


class MeetingMinutesCreate(BaseModel):
    """创建会议纪要（入口数据）。"""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, description="会议主题")
    held_at: datetime | None = None
    participants: list[str] = Field(default_factory=list)
    location: str | None = None
    recording_url: str | None = None
    agenda: list[str] = Field(default_factory=list)
    module: str | None = None
    raw_text: str = Field(min_length=1, description="原始纪要全文")
    summary: str = ""
    decisions: list[dict] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    submitted_by: str = "maintainer"
    source_ref: str | None = None


class Attachment(BaseModel):
    """通用附件/链接登记：方案上传自动管理链接，跨 task/bug/meeting 复用。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, description="所属工作区")
    entity: str  # task | bug | meeting | requirement
    entity_id: str
    url: str = Field(min_length=1, description="文件/链接地址")
    name: str = ""
    mime: str | None = None
    size: int = 0
    uploaded_by: str = "maintainer"
    created_at: datetime
