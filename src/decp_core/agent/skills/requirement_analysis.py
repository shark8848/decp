"""需求收集-整理-分析技能（编排验证）。

与 skills/requirement-analysis/SKILL.md 对应：
本 Python 类用于**进程内编排验证**（direct 模式），
外部成熟 Agent Runtime 通过读取 SKILL.md 了解流程、通过 MCP 协议调用同一批工具。
"""
from __future__ import annotations

from typing import Any

from decp_core.agent.skills.base import BaseSkill


class RequirementAnalysisSkill(BaseSkill):
    """产品需求收集、整理与分析技能（核心业务闭环：反馈 → 分析 → 审核 → 入库）。"""

    name = "requirement_analysis"
    description = (
        "产品需求收集、整理与分析：收集客户反馈、结构化、去重、聚类、影响分析、"
        "优先级建议、生成需求草稿、提交审核、生成 HTML/Excel 报告。"
        "适用于维护人员提交反馈、产品经理整理分析需求、查看下载结果的场景。"
    )
    tools_required = [
        "feedback.submit",
        "feedback.search",
        "requirement.analyze",
        "requirement.generate_draft",
        "requirement.review",
        "requirement.search",
        "report.generate_html",
        "report.generate_excel",
    ]

    async def run(self, **params: Any) -> Any:
        action = params.get("action", "full")
        result: dict[str, Any] = {"skill": self.name, "action": action}

        # 1) 收集反馈（可选）
        if params.get("submit_feedback"):
            content = params["submit_feedback"]
            fb = await self._call("feedback.submit", content=content, **self._fb_kwargs(params))
            result["submitted"] = fb

        # 2) 整理与分析
        analysis = await self._call(
            "requirement.analyze",
            customer=params.get("customer"),
            module=params.get("module"),
        )
        result["analysis"] = analysis

        # 3) 生成草稿（full / draft 动作）
        if action in ("full", "draft"):
            draft = await self._call(
                "requirement.generate_draft",
                title=params.get("title"),
                module=params.get("module"),
                priority=params.get("priority"),
                customer=params.get("customer"),
            )
            result["draft"] = draft

        # 4) 审核（可选，产品经理决策）
        if params.get("decision") and params.get("requirement_id"):
            review = await self._call(
                "requirement.review",
                requirement_id=params["requirement_id"],
                decision=params["decision"],
                reviewer=params.get("reviewer", "product_manager"),
            )
            result["review"] = review

        # 5) 报告导出（可查看/下载）
        if action in ("full", "report"):
            result["report_html"] = await self._call("report.generate_html", title=params.get("report_title", "产品需求收集、整理与分析报告"))
            result["report_excel"] = await self._call("report.generate_excel")

        return result

    @staticmethod
    def _fb_kwargs(params: dict[str, Any]) -> dict[str, Any]:
        return {
            k: params[k]
            for k in ("customer", "module", "feedback_type", "impact", "source_ref", "channel")
            if params.get(k) is not None
        }
