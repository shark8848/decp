# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""MCP 工具注册：企业数据层 Service 封装为 MCP tools。

按设计文档，工具分为：
- feedback.* 反馈数据域：提交、查询
- requirement.* 需求数据域：分析、草稿、入库、审核、查询
- report.* 报告导出：HTML / Excel（供产品经理与收集人员在 agent 中下载查看）
"""
from __future__ import annotations

from decp_core.mcp_ import utils
from decp_core.models import FeedbackCreate, RequirementCreate, utcnow
from decp_core.report import ReportService
from decp_core.services import FeedbackService, RequirementService
from decp_core.storage.base import StorageBackend


class DecpTools:
    """MCP 工具注册器：持有 Service 实例，注册全部工具。"""

    # 标准工具名（点分命名，MCP 层与 Skill 层共用）→ 方法
    TOOL_BINDINGS: dict[str, str] = {
        "feedback.submit": "feedback_submit",
        "feedback.search": "feedback_search",
        "feedback.get": "feedback_get",
        "requirement.analyze": "requirement_analyze",
        "requirement.generate_draft": "requirement_generate_draft",
        "requirement.create": "requirement_create",
        "requirement.review": "requirement_review",
        "requirement.find_similar": "requirement_find_similar",
        "requirement.search": "requirement_search",
        "requirement.get": "requirement_get",
        "report.generate_html": "report_generate_html",
        "report.generate_excel": "report_generate_excel",
        "domain.stats": "domain_stats",
    }

    def __init__(self, storage: StorageBackend, reports_dir: str) -> None:
        self.storage = storage
        self.feedback = FeedbackService(storage)
        self.requirement = RequirementService(storage, self.feedback)
        self.reports = ReportService(reports_dir)

    def tool_callable(self, name: str):
        """按标准工具名取可调用方法。"""
        method = self.TOOL_BINDINGS.get(name)
        if method is None:
            return None
        return getattr(self, method, None)

    # ================= feedback 数据域 =================

    async def feedback_submit(self, content: str, channel: str = "natural_language",
                              customer: str | None = None, module: str | None = None,
                              feedback_type: str | None = None, impact: str | None = None,
                              source_ref: str | None = None, submitted_by: str = "maintainer") -> dict:
        """收集一条客户反馈（自然语言 / 工单 / Excel 行），返回结构化结果。"""
        try:
            fb = await self.feedback.create(FeedbackCreate(
                content=content, channel=channel, customer=customer, module=module,
                feedback_type=feedback_type, impact=impact, source_ref=source_ref,
                submitted_by=submitted_by,
            ))
            return utils.tool_result({
                "ok": True, "id": fb.id,
                "structured": fb.structured,
            })
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"提交反馈失败: {e}")

    async def feedback_search(self, customer: str | None = None, module: str | None = None,
                              limit: int = 50, offset: int = 0) -> dict:
        """查询反馈列表（支持按客户/模块过滤），返回最小必要字段。"""
        try:
            items = await self.feedback.list(customer=customer, module=module, limit=limit, offset=offset)
            return utils.tool_result({
                "count": len(items),
                "items": [
                    {
                        "id": f.id, "content": f.content, "customer": f.customer,
                        "module": f.module, "channel": f.channel,
                        "feedback_type": f.structured.get("feedback_type"),
                        "impact_severity": f.structured.get("impact_severity"),
                        "created_at": f.created_at.isoformat(),
                    }
                    for f in items
                ],
            })
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"查询反馈失败: {e}")

    async def feedback_get(self, feedback_id: str) -> dict:
        """按 id 获取单条反馈完整信息。"""
        try:
            fb = await self.feedback.get(feedback_id)
            if fb is None:
                return utils.tool_result({"ok": False, "error": "反馈不存在"}, is_error=True)
            return utils.tool_result({"ok": True, "feedback": fb.model_dump()})
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"获取反馈失败: {e}")

    # ================= requirement 数据域 =================

    async def requirement_analyze(self, customer: str | None = None, module: str | None = None,
                                  limit: int = 200, offset: int = 0) -> dict:
        """对反馈集合执行整理与分析：分类、去重、聚类、影响分析、优先级建议、来源校验。"""
        try:
            analysis = await self.requirement.analyze(customer=customer, module=module, limit=limit, offset=offset)
            return utils.tool_result(analysis.model_dump())
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"分析失败: {e}")

    async def requirement_generate_draft(self, title: str | None = None,
                                         description: str | None = None,
                                         module: str | None = None,
                                         priority: str | None = None,
                                         feedback_ids: list[str] | None = None,
                                         customer: str | None = None) -> dict:
        """基于分析结果生成需求草稿（REQ-xxx, 状态 Draft），携带来源引用与置信度。"""
        try:
            req = await self.requirement.generate_draft(
                title=title, description=description, module=module, priority=priority,
                feedback_ids=feedback_ids, customer=customer,
            )
            return utils.tool_result({"ok": True, "requirement": req.model_dump()})
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"生成需求草稿失败: {e}")

    async def requirement_create(self, title: str, description: str = "",
                                 module: str | None = None, priority: str = "P2",
                                 feedback_ids: list[str] | None = None,
                                 source_refs: list[dict] | None = None,
                                 confidence: float = 0.0) -> dict:
        """正式写入一条需求对象（Schema 校验 + 版本化入库）。"""
        try:
            req = await self.requirement.create(RequirementCreate(
                title=title, description=description, module=module,
                priority=priority, status="draft", feedback_ids=feedback_ids or [],
                source_refs=source_refs or [], confidence=confidence,
            ))
            return utils.tool_result({"ok": True, "requirement": req.model_dump()})
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"写入需求失败: {e}")

    async def requirement_review(self, requirement_id: str, decision: str, reviewer: str) -> dict:
        """产品经理审核需求草稿：accept(接受) / reject(拒绝) / merge(合并)；人工审批，版本递增。"""
        try:
            req = await self.requirement.review(requirement_id, decision, reviewer)
            return utils.tool_result({"ok": True, "requirement": req.model_dump()})
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"审核需求失败: {e}")

    async def requirement_find_similar(self, text: str, limit: int = 10) -> dict:
        """查找与给定文本相似的历史反馈（去重/查重入口）。"""
        try:
            items = await self.feedback.list(limit=500)
            scored = []
            for f in items:
                s = self.requirement_sim(f.content, text)
                if s > 0.2:
                    scored.append({"feedback_id": f.id, "content": f.content[:80], "score": round(s, 3)})
            scored.sort(key=lambda x: x["score"], reverse=True)
            return utils.tool_result({"query": text, "matches": scored[:limit], "total": len(scored)})
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"查找相似反馈失败: {e}")

    @staticmethod
    def requirement_sim(a: str, b: str) -> float:
        from decp_core.services import similarity
        return similarity(a, b)

    async def requirement_search(self, status: str | None = None, priority: str | None = None,
                                 module: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        """查询需求列表（支持按状态/优先级/模块过滤）。"""
        try:
            items = await self.requirement.list(status=status, priority=priority, module=module, limit=limit, offset=offset)
            return utils.tool_result({
                "count": len(items),
                "items": [r.model_dump() for r in items],
            })
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"查询需求失败: {e}")

    async def requirement_get(self, requirement_id: str) -> dict:
        """按 id 获取需求完整信息。"""
        try:
            req = await self.requirement.get(requirement_id)
            if req is None:
                return utils.tool_result({"ok": False, "error": "需求不存在"}, is_error=True)
            return utils.tool_result({"ok": True, "requirement": req.model_dump()})
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"获取需求失败: {e}")

    # ================= report 数据域 =================

    async def report_generate_html(self, title: str = "产品需求收集、整理与分析报告",
                                   customer: str | None = None) -> dict:
        """生成 HTML 分析报告，返回可下载的本地路径。"""
        try:
            feedbacks = await self.feedback.list(customer=customer, limit=500)
            requirements = await self.requirement.list(limit=500)
            analysis = await self.requirement.analyze(customer=customer, limit=500)
            path = await self.reports.build_html_report(feedbacks, requirements, analysis, title=title)
            return utils.tool_result({"ok": True, "path": str(path), "type": "html"})
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"生成 HTML 报告失败: {e}")

    async def report_generate_excel(self) -> dict:
        """生成 Excel 报表（需求清单/反馈明细/聚类分析），返回可下载的本地路径。"""
        try:
            requirements = await self.requirement.list(limit=1000)
            feedbacks = await self.feedback.list(limit=1000)
            analysis = await self.requirement.analyze(limit=1000)
            path = await self.reports.build_excel_report(requirements, feedbacks, analysis)
            return utils.tool_result({"ok": True, "path": str(path), "type": "excel"})
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"生成 Excel 报表失败: {e}")

    async def domain_stats(self) -> dict:
        """数据域统计：feedback / requirement 数量与存储后端信息。"""
        try:
            return utils.tool_result(await self.storage.domain_stats())
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"获取数据域统计失败: {e}")


def register_all_tools(server, storage: StorageBackend, reports_dir: str) -> DecpTools:
    """把 DecpTools 的全部方法注册为 MCP tools，返回实例供数字员工 Skill 直调。

    工具名与描述由 DecpTools.TOOL_BINDINGS + _TOOL_DESCS 统一定义，
    Skill 层（direct 模式）与 MCP 层共用同一命名，保证跨模式一致。
    """
    tools = DecpTools(storage, reports_dir)
    for name in DecpTools.TOOL_BINDINGS:
        fn = tools.tool_callable(name)
        if fn is None:
            raise RuntimeError(f"工具方法缺失: {name}")
        server.add_tool(fn, name=name, description=_TOOL_DESCS.get(name, ""))
    return tools


_TOOL_DESCS: dict[str, str] = {
    "feedback.submit": "提交客户反馈（自然语言/工单/Excel 行），完成结构化抽取",
    "feedback.search": "查询反馈列表，支持按客户/模块过滤",
    "feedback.get": "按 id 获取单条反馈完整信息",
    "requirement.analyze": "对反馈集合执行整理与分析：分类、去重、聚类、影响分析、优先级建议、来源校验",
    "requirement.generate_draft": "基于分析结果生成需求草稿（状态 Draft）",
    "requirement.create": "正式写入需求对象（版本化入库，Schema 校验）",
    "requirement.review": "产品经理审核需求：accept/reject/merge，人工审批",
    "requirement.find_similar": "查找与给定文本相似的历史反馈（查重）",
    "requirement.search": "查询需求列表，支持按状态/优先级/模块过滤",
    "requirement.get": "按 id 获取需求完整信息",
    "report.generate_html": "生成 HTML 分析报告，返回本地可下载路径",
    "report.generate_excel": "生成 Excel 报表（需求清单/反馈明细/聚类），返回本地可下载路径",
    "domain.stats": "数据域统计：feedback/requirement 数量与后端信息",
}
