"""查询技能（编排验证）。

与 skills/requirement-query/SKILL.md 对应：
本 Python 类用于进程内编排验证，外部 Agent Runtime 通过 MCP 协议调用同一批工具。
"""
from __future__ import annotations

from typing import Any

from decp_core.agent.skills.base import BaseSkill


class QuerySkill(BaseSkill):
    """需求/反馈查询技能：产品经理与收集人员通过自然语言指令查看数据（只读）。"""

    name = "query"
    description = (
        "查询客户反馈与需求：按客户、模块、状态、优先级筛选；"
        "查看需求详情、相似反馈；适用于『查看最近的反馈』『这个需求怎么样了』等指令。"
    )
    tools_required = ["feedback.search", "requirement.search", "requirement.get", "requirement.find_similar"]

    async def run(self, **params: Any) -> Any:
        result: dict[str, Any] = {"skill": self.name}
        want_reqs = params.get("requirements") or params.get("status") or params.get("priority") or params.get("list_requirements")
        want_fbs = params.get("feedbacks") or params.get("customer") or params.get("list_feedbacks")

        # 查询需求
        if want_reqs:
            result["requirements"] = await self._call(
                "requirement.search",
                status=params.get("status"),
                priority=params.get("priority"),
                module=params.get("module"),
                limit=params.get("limit", 50),
            )

        # 查询反馈
        if want_fbs:
            result["feedbacks"] = await self._call(
                "feedback.search",
                customer=params.get("customer"),
                module=params.get("module"),
                limit=params.get("limit", 50),
            )

        # 未指定查询目标时默认返回需求+反馈列表（产品经理/收集人员常用）
        if not (want_reqs or want_fbs) and not params.get("text") and not params.get("requirement_id"):
            result["requirements"] = await self._call("requirement.search", limit=params.get("limit", 50))
            result["feedbacks"] = await self._call("feedback.search", limit=params.get("limit", 50))

        # 相似反馈查重
        if params.get("text"):
            result["similar"] = await self._call("requirement.find_similar", text=params["text"], limit=params.get("limit", 10))

        # 需求详情
        if params.get("requirement_id"):
            result["detail"] = await self._call("requirement.get", requirement_id=params["requirement_id"])

        return result
