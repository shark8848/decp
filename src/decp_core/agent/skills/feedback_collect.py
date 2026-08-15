# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""客户反馈收集技能（编排验证）。

与 skills/feedback-collect/SKILL.md 对应：
本 Python 类用于进程内编排验证，外部 Agent Runtime 通过 MCP 协议调用同一批工具。
"""
from __future__ import annotations

from typing import Any

from decp_core.agent.skills.base import BaseSkill


class FeedbackCollectSkill(BaseSkill):
    """客户反馈收集与结构化：录入反馈原文，完成结构化理解，进入 feedback 数据域。"""

    name = "feedback_collect"
    description = (
        "客户反馈收集与结构化：录入自然语言/工单/Excel 反馈，"
        "完成结构化理解（客户/模块/类型/影响），进入 feedback 数据域。"
        "适用于维护人员/客服/客户成功录入客户反馈的场景。"
    )
    tools_required = ["feedback.submit"]

    async def run(self, **params: Any) -> Any:
        content = params.get("content") or params.get("submit_feedback")
        if not content:
            return {"skill": self.name, "error": "缺少反馈内容 content"}
        result = await self._call("feedback.submit", content=content, **self._kwargs(params))
        return {"skill": self.name, "submitted": result}

    @staticmethod
    def _kwargs(params: dict[str, Any]) -> dict[str, Any]:
        return {
            k: params[k]
            for k in ("customer", "module", "feedback_type", "impact", "source_ref", "channel", "submitted_by")
            if params.get(k) is not None
        }
