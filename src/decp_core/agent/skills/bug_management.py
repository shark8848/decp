# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""缺陷管理技能（全生命周期 + 多域关联）。

与 skills/bug-management/SKILL.md 对应：
本 Python 类用于**进程内编排验证**（direct 模式），
外部成熟 Agent Runtime 通过读取 SKILL.md 了解流程、通过 MCP 协议调用同一批工具。
"""
from __future__ import annotations

from typing import Any

from decp_core.agent.skills.base import BaseSkill


class BugManagementSkill(BaseSkill):
    """缺陷管理：创建/分诊/修复/验证/关闭，关联反馈/需求/任务/会议。"""

    name = "bug_management"
    description = (
        "缺陷管理：报告/创建缺陷、确认可复现、开始修复、标记修复待验证、验证通过关闭、"
        "标记不修复（wonfix）、从客户反馈转缺陷、关联需求/开发任务/会议、上传修复方案。"
        "适用于研发缺陷跟踪、质量保障、故障处理场景。"
    )
    tools_required = [
        "bug.create",
        "bug.transition",
        "bug.search",
        "bug.get",
        "bug.link",
        "bug.from_feedback",
        "bug.upload_plan",
        "task.create",
    ]

    async def run(self, **params: Any) -> Any:
        action = params.get("action", "search")
        result: dict[str, Any] = {"skill": self.name, "action": action}

        # 1) 创建缺陷
        if params.get("create_bug"):
            result["created"] = await self._call(
                "bug.create",
                title=params["create_bug"],
                description=params.get("description"),
                severity=params.get("severity", "medium"),
                priority=params.get("priority"),
                channel=params.get("channel", "manual"),
                environment=params.get("environment"),
                reproduce_steps=params.get("reproduce_steps"),
                expected=params.get("expected"),
                actual=params.get("actual"),
                assignee=params.get("assignee"),
                feedback_ids=params.get("feedback_ids"),
            )

        # 2) 状态流转
        if params.get("transition_bug") and params.get("status"):
            result["transitioned"] = await self._call(
                "bug.transition",
                bug_id=params["transition_bug"],
                status=params["status"],
                comment=params.get("comment"),
            )

        # 3) 多域关联
        if params.get("link_bug"):
            result["linked"] = await self._call(
                "bug.link",
                bug_id=params["link_bug"],
                feedback_ids=params.get("feedback_ids"),
                requirement_ids=params.get("requirement_ids"),
                task_ids=params.get("task_ids"),
                meeting_ids=params.get("meeting_ids"),
            )

        # 4) 客户反馈转缺陷
        if params.get("from_feedback"):
            result["from_feedback"] = await self._call(
                "bug.from_feedback",
                feedback_id=params["from_feedback"],
            )

        # 5) 上传修复方案
        if params.get("upload_plan") and params.get("plan_url"):
            result["plan"] = await self._call(
                "bug.upload_plan",
                bug_id=params["upload_plan"],
                url=params["plan_url"],
                name=params.get("plan_name"),
            )

        # 6) 搜索 / 详情
        if action in ("search", "full"):
            result["bugs"] = await self._call(
                "bug.search",
                status=params.get("status"),
                severity=params.get("severity"),
                priority=params.get("priority"),
                assignee=params.get("assignee"),
                module=params.get("module"),
                channel=params.get("channel"),
            )
        if params.get("bug_id"):
            result["bug"] = await self._call("bug.get", bug_id=params["bug_id"])

        return result
