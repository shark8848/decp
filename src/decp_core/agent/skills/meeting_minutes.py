# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""会议纪要管理技能（提交 → 提取 → 存档 → 待办任务化/缺陷识别）。

与 skills/meeting-minutes/SKILL.md 对应：
本 Python 类用于**进程内编排验证**（direct 模式），
外部成熟 Agent Runtime 通过读取 SKILL.md 了解流程、通过 MCP 协议调用同一批工具。
"""
from __future__ import annotations

from typing import Any

from decp_core.agent.skills.base import BaseSkill


class MeetingMinutesSkill(BaseSkill):
    """会议纪要：提交原文、提取决议/待办/关键词、待办批量转任务、缺陷识别。"""

    name = "meeting_minutes"
    description = (
        "会议纪要管理：提交会议纪要原文并自动提取摘要、决议、待办（责任人/截止/开发或事务分类）、"
        "将会议待办批量列入任务计划（分开发任务、技术债、运营任务、事务任务）、"
        "识别纪要中的缺陷并创建缺陷单。"
        "适用于会议记录沉淀、行动项跟踪、跨团队协调场景。"
    )
    tools_required = [
        "meeting.submit",
        "meeting.get",
        "meeting.list",
        "meeting.to_tasks",
        "meeting.to_bugs",
        "task.create",
    ]

    async def run(self, **params: Any) -> Any:
        action = params.get("action", "submit")
        result: dict[str, Any] = {"skill": self.name, "action": action}

        # 1) 提交纪要 → 提取 + 存档
        if params.get("submit_meeting"):
            result["submitted"] = await self._call(
                "meeting.submit",
                title=params.get("title", "会议纪要"),
                raw_text=params["submit_meeting"],
                held_at=params.get("held_at"),
                participants=params.get("participants"),
                location=params.get("location"),
                recording_url=params.get("recording_url"),
                module=params.get("module"),
            )

        # 2) 待办批量转任务（默认预览，确认后入库）
        if params.get("to_tasks"):
            result["tasks"] = await self._call(
                "meeting.to_tasks",
                meeting_id=params["to_tasks"],
                dry_run=params.get("dry_run", True),
            )

        # 3) 纪要缺陷识别
        if params.get("to_bugs"):
            result["bugs"] = await self._call(
                "meeting.to_bugs",
                meeting_id=params["to_bugs"],
                dry_run=params.get("dry_run", True),
            )

        # 4) 查询
        if params.get("meeting_id"):
            result["meeting"] = await self._call("meeting.get", meeting_id=params["meeting_id"])
        if action in ("list", "full"):
            result["meetings"] = await self._call(
                "meeting.list",
                module=params.get("module"),
                limit=params.get("limit", 50),
            )

        return result
