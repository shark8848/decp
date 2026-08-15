# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""领域模型：feedback（反馈）与 requirement（需求）两大数据域。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
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


class SourceRef(BaseModel):
    """来源追踪：需求/反馈的可追溯引用。"""

    model_config = ConfigDict(extra="ignore")

    ref_type: Literal["feedback", "ticket", "excel", "api", "manual"]
    ref_id: str
    detail: str | None = None


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
    version: int = 1
    created_at: datetime
    updated_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None


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
