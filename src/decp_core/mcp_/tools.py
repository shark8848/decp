# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""MCP 工具注册：企业数据层 Service 封装为 MCP tools。

按设计文档，工具分为：
- feedback.* 反馈数据域：提交、查询
- requirement.* 需求数据域：分析、草稿、入库、审核、查询
- report.* 报告导出：HTML / Excel（供产品经理与收集人员在 agent 中下载查看）
"""
from __future__ import annotations

from mcp.server.mcpserver.context import Context

from decp_core.mcp_ import utils
from decp_core.mcp_.context_injection import resolve_identity
from decp_core.models import FeedbackCreate, RequirementCreate, utcnow
from decp_core.report import ReportService
from decp_core.services import (
    FeedbackService,
    RequirementService,
    WorkspaceError,
    WorkspaceService,
)
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
        "requirement.archive": "requirement_archive",
        "requirement.restore": "requirement_restore",
        "requirement.find_similar": "requirement_find_similar",
        "requirement.search": "requirement_search",
        "requirement.get": "requirement_get",
        "report.generate_html": "report_generate_html",
        "report.generate_excel": "report_generate_excel",
        "domain.stats": "domain_stats",
        "workspace.create": "workspace_create",
        "workspace.join": "workspace_join",
        "workspace.join_by_passcode": "workspace_join_by_passcode",
        "workspace.approve_member": "workspace_approve_member",
        "workspace.reject_member": "workspace_reject_member",
        "workspace.list": "workspace_list",
        "workspace.get": "workspace_get",
        "workspace.members": "workspace_members",
    }

    def __init__(self, storage: StorageBackend, reports_dir: str) -> None:
        self.storage = storage
        self.feedback = FeedbackService(storage)
        self.requirement = RequirementService(storage, self.feedback)
        self.workspace = WorkspaceService(storage)
        self.reports = ReportService(reports_dir)
        self._default_ensured = False

    async def _ensure_default(self) -> None:
        """惰性保障默认工作区（首次工具调用时执行一次）。"""
        if not self._default_ensured:
            await self.workspace.ensure_default()
            self._default_ensured = True

    def tool_callable(self, name: str):
        """按标准工具名取可调用方法。"""
        method = self.TOOL_BINDINGS.get(name)
        if method is None:
            return None
        return getattr(self, method, None)

    # ---- 身份解析与成员资格（多工作区隔离核心） ----
    async def _identity(self, ctx, user_id: str | None = None, workspace_id: str | None = None) -> tuple[str, str]:
        """解析调用者身份：(user_id, workspace_id)，显式参数 > ctx.meta > 默认身份。"""
        return resolve_identity(ctx=ctx, user_id=user_id, workspace_id=workspace_id)

    async def _authorize(self, ctx, user_id: str | None = None, workspace_id: str | None = None) -> tuple[str, str]:
        """解析身份并校验 workspace 成员资格；非成员抛 WorkspaceError。"""
        await self._ensure_default()
        uid, wid = await self._identity(ctx, user_id, workspace_id)
        await self.workspace.assert_member(wid, uid)
        return uid, wid

    # ================= feedback 数据域 =================

    async def feedback_submit(self, content: str, channel: str = "natural_language",
                              customer: str | None = None, module: str | None = None,
                              feedback_type: str | None = None, impact: str | None = None,
                              source_ref: str | None = None, submitted_by: str = "maintainer",
                              ctx: Context | None = None,
                              user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """收集一条客户反馈（自然语言 / 工单 / Excel 行），返回结构化结果。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            fb = await self.feedback.create(FeedbackCreate(
                content=content, channel=channel, customer=customer, module=module,
                feedback_type=feedback_type, impact=impact, source_ref=source_ref,
                submitted_by=submitted_by,
            ), workspace_id=wid)
            return utils.tool_result({
                "ok": True, "id": fb.id,
                "structured": fb.structured,
                "workspace_id": wid, "user_id": uid,
            })
        except WorkspaceError as e:
            return utils.error_result(f"提交反馈失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"提交反馈失败: {e}")

    async def feedback_search(self, customer: str | None = None, module: str | None = None,
                              limit: int = 50, offset: int = 0,
                              ctx: Context | None = None,
                              user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """查询反馈列表（支持按客户/模块过滤），返回最小必要字段。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            items = await self.feedback.list(
                customer=customer, module=module, limit=limit, offset=offset,
                workspace_id=wid,
            )
            return utils.tool_result({
                "count": len(items),
                "workspace_id": wid, "user_id": uid,
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
        except WorkspaceError as e:
            return utils.error_result(f"查询反馈失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"查询反馈失败: {e}")

    async def feedback_get(self, feedback_id: str,
                           ctx: Context | None = None,
                           user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """按 id 获取单条反馈完整信息。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            fb = await self.feedback.get(feedback_id, workspace_id=wid)
            if fb is None:
                return utils.tool_result({"ok": False, "error": "反馈不存在"}, is_error=True)
            return utils.tool_result({"ok": True, "workspace_id": wid, "user_id": uid,
                                      "feedback": fb.model_dump()})
        except WorkspaceError as e:
            return utils.error_result(f"获取反馈失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"获取反馈失败: {e}")

    # ================= requirement 数据域 =================

    async def requirement_analyze(self, customer: str | None = None, module: str | None = None,
                                  limit: int = 200, offset: int = 0,
                                  ctx: Context | None = None,
                                  user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """对反馈集合执行整理与分析：分类、去重、聚类、影响分析、优先级建议、来源校验。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            analysis = await self.requirement.analyze(
                customer=customer, module=module, limit=limit, offset=offset,
                workspace_id=wid,
            )
            return utils.tool_result({
                "workspace_id": wid, "user_id": uid,
                **analysis.model_dump(),
            })
        except WorkspaceError as e:
            return utils.error_result(f"分析失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"分析失败: {e}")

    async def requirement_generate_draft(self, title: str | None = None,
                                         description: str | None = None,
                                         module: str | None = None,
                                         priority: str | None = None,
                                         feedback_ids: list[str] | None = None,
                                         customer: str | None = None,
                                         ctx: Context | None = None,
                                         user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """基于分析结果生成需求草稿（REQ-xxx, 状态 Draft），携带来源引用与置信度。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            req = await self.requirement.generate_draft(
                title=title, description=description, module=module, priority=priority,
                feedback_ids=feedback_ids, customer=customer, workspace_id=wid,
            )
            return utils.tool_result({"ok": True, "workspace_id": wid, "user_id": uid,
                                      "requirement": req.model_dump()})
        except WorkspaceError as e:
            return utils.error_result(f"生成需求草稿失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"生成需求草稿失败: {e}")

    async def requirement_create(self, title: str, description: str = "",
                                 module: str | None = None, priority: str = "P2",
                                 feedback_ids: list[str] | None = None,
                                 source_refs: list[dict] | None = None,
                                 confidence: float = 0.0,
                                 ctx: Context | None = None,
                                 user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """正式写入一条需求对象（Schema 校验 + 版本化入库）。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            req = await self.requirement.create(RequirementCreate(
                title=title, description=description, module=module,
                priority=priority, status="draft", feedback_ids=feedback_ids or [],
                source_refs=source_refs or [], confidence=confidence,
            ), workspace_id=wid)
            return utils.tool_result({"ok": True, "workspace_id": wid, "user_id": uid,
                                      "requirement": req.model_dump()})
        except WorkspaceError as e:
            return utils.error_result(f"写入需求失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"写入需求失败: {e}")

    async def requirement_review(self, requirement_id: str, decision: str, reviewer: str,
                                 ctx: Context | None = None,
                                 user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """产品经理审核需求草稿：accept(接受) / reject(拒绝) / merge(合并)；人工审批，版本递增。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            req = await self.requirement.review(requirement_id, decision, reviewer, workspace_id=wid)
            return utils.tool_result({"ok": True, "workspace_id": wid, "user_id": uid,
                                      "requirement": req.model_dump()})
        except WorkspaceError as e:
            return utils.error_result(f"审核需求失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"审核需求失败: {e}")

    async def requirement_archive(self, requirement_id: str, archived_by: str = "maintainer",
                                  ctx: Context | None = None,
                                  user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """归档需求：仅已审核完结（accepted/rejected/merged）可归档，移出活跃视图，可恢复。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            req = await self.requirement.archive(requirement_id, archived_by, workspace_id=wid)
            return utils.tool_result({"ok": True, "workspace_id": wid, "user_id": uid,
                                      "requirement": req.model_dump()})
        except WorkspaceError as e:
            return utils.error_result(f"归档需求失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"归档需求失败: {e}")

    async def requirement_restore(self, requirement_id: str,
                                  ctx: Context | None = None,
                                  user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """恢复已归档需求：清除归档标记，保留状态/版本/审核历史。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            req = await self.requirement.restore(requirement_id, workspace_id=wid)
            return utils.tool_result({"ok": True, "workspace_id": wid, "user_id": uid,
                                      "requirement": req.model_dump()})
        except WorkspaceError as e:
            return utils.error_result(f"恢复需求失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"恢复需求失败: {e}")

    async def requirement_find_similar(self, text: str, limit: int = 10,
                                       ctx: Context | None = None,
                                       user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """查找与给定文本相似的历史反馈（去重/查重入口）。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            items = await self.feedback.list(limit=500, workspace_id=wid)
            scored = []
            for f in items:
                s = self.requirement_sim(f.content, text)
                if s > 0.2:
                    scored.append({"feedback_id": f.id, "content": f.content[:80], "score": round(s, 3)})
            scored.sort(key=lambda x: x["score"], reverse=True)
            return utils.tool_result({"query": text, "workspace_id": wid, "user_id": uid,
                                      "matches": scored[:limit], "total": len(scored)})
        except WorkspaceError as e:
            return utils.error_result(f"查找相似反馈失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"查找相似反馈失败: {e}")

    @staticmethod
    def requirement_sim(a: str, b: str) -> float:
        from decp_core.services import similarity
        return similarity(a, b)

    async def requirement_search(self, status: str | None = None, priority: str | None = None,
                                 module: str | None = None, limit: int = 50, offset: int = 0,
                                 include_archived: bool = False,
                                 ctx: Context | None = None,
                                 user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """查询需求列表（支持按状态/优先级/模块过滤；include_archived 含已归档）。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            items = await self.requirement.list(
                status=status, priority=priority, module=module,
                limit=limit, offset=offset, include_archived=include_archived,
                workspace_id=wid,
            )
            return utils.tool_result({
                "count": len(items),
                "workspace_id": wid, "user_id": uid,
                "items": [r.model_dump() for r in items],
            })
        except WorkspaceError as e:
            return utils.error_result(f"查询需求失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"查询需求失败: {e}")

    async def requirement_get(self, requirement_id: str,
                              ctx: Context | None = None,
                              user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """按 id 获取需求完整信息。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            req = await self.requirement.get(requirement_id, workspace_id=wid)
            if req is None:
                return utils.tool_result({"ok": False, "error": "需求不存在"}, is_error=True)
            return utils.tool_result({"ok": True, "workspace_id": wid, "user_id": uid,
                                      "requirement": req.model_dump()})
        except WorkspaceError as e:
            return utils.error_result(f"获取需求失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"获取需求失败: {e}")

    # ================= report 数据域 =================

    async def report_generate_html(self, title: str = "产品需求收集、整理与分析报告",
                                   customer: str | None = None,
                                   ctx: Context | None = None,
                                   user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """生成 HTML 分析报告，返回可下载的本地路径。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            feedbacks = await self.feedback.list(customer=customer, limit=500, workspace_id=wid)
            requirements = await self.requirement.list(limit=500, workspace_id=wid)
            analysis = await self.requirement.analyze(customer=customer, limit=500, workspace_id=wid)
            path = await self.reports.build_html_report(feedbacks, requirements, analysis, title=title)
            return utils.tool_result({"ok": True, "path": str(path), "type": "html",
                                      "workspace_id": wid, "user_id": uid})
        except WorkspaceError as e:
            return utils.error_result(f"生成 HTML 报告失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"生成 HTML 报告失败: {e}")

    async def report_generate_excel(self,
                                    ctx: Context | None = None,
                                    user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """生成 Excel 报表（需求清单/反馈明细/聚类分析），返回可下载的本地路径。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            requirements = await self.requirement.list(limit=1000, workspace_id=wid)
            feedbacks = await self.feedback.list(limit=1000, workspace_id=wid)
            analysis = await self.requirement.analyze(limit=1000, workspace_id=wid)
            path = await self.reports.build_excel_report(requirements, feedbacks, analysis)
            return utils.tool_result({"ok": True, "path": str(path), "type": "excel",
                                      "workspace_id": wid, "user_id": uid})
        except WorkspaceError as e:
            return utils.error_result(f"生成 Excel 报表失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"生成 Excel 报表失败: {e}")

    async def domain_stats(self, ctx: Context | None = None,
                           user_id: str | None = None, workspace_id: str | None = None) -> dict:
        """数据域统计：feedback / requirement 数量与存储后端信息（按调用者工作区口径）。"""
        try:
            uid, wid = await self._authorize(ctx, user_id, workspace_id)
            fb = await self.feedback.count(workspace_id=wid)
            req = await self.requirement.count(workspace_id=wid)
            stats = await self.storage.domain_stats()
            return utils.tool_result({
                "feedback": fb, "requirement": req,
                "backend": stats.get("backend"), "path": stats.get("path"),
                "workspace_id": wid, "user_id": uid,
            })
        except WorkspaceError as e:
            return utils.error_result(f"获取数据域统计失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"获取数据域统计失败: {e}")

    # ================= workspace 数据域（多租户隔离） =================

    async def workspace_create(self, name: str, description: str = "",
                               ctx: Context | None = None,
                               user_id: str | None = None) -> dict:
        """创建产品 workspace，创建者自动成为 owner（已批准成员）。"""
        try:
            uid, _ = await self._identity(ctx, user_id)
            ws = await self.workspace.create(name, uid, description)
            return utils.tool_result({"ok": True, "workspace": ws})
        except WorkspaceError as e:
            return utils.error_result(f"创建工作区失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"创建工作区失败: {e}")

    async def workspace_join(self, workspace_id: str,
                             ctx: Context | None = None,
                             user_id: str | None = None) -> dict:
        """申请加入 workspace（pending，等待 owner 审批）。"""
        try:
            uid, _ = await self._identity(ctx, user_id)
            m = await self.workspace.join(workspace_id, uid)
            return utils.tool_result({"ok": True, "workspace_id": workspace_id,
                                      "status": m["status"], "user_id": uid})
        except WorkspaceError as e:
            return utils.error_result(f"申请加入失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"申请加入失败: {e}")

    async def workspace_join_by_passcode(self, workspace_id: str, passcode: str,
                                         ctx: Context | None = None,
                                         user_id: str | None = None) -> dict:
        """凭工作区通行证直接加入（凭证式授权，绕过 owner 审批）。

        通行证不绑定调用者身份：任何持有者凭正确 passcode 即可加入为已批准 member。
        通行证由 owner 通过 workspace.get 获取；校验失败返回错误。
        """
        try:
            uid, _ = await self._identity(ctx, user_id)
            m = await self.workspace.join_by_passcode(workspace_id, passcode, uid)
            return utils.tool_result({"ok": True, "workspace_id": workspace_id,
                                      "user_id": uid, "status": m["status"],
                                      "joined_via": "passcode"})
        except WorkspaceError as e:
            return utils.error_result(f"凭通行证加入失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"凭通行证加入失败: {e}")

    async def workspace_approve_member(self, workspace_id: str, target_user_id: str,
                                       ctx: Context | None = None,
                                       user_id: str | None = None) -> dict:
        """owner 审批通过成员加入申请。仅 owner 可操作。"""
        try:
            uid, _ = await self._identity(ctx, user_id)
            m = await self.workspace.approve_member(workspace_id, target_user_id, uid)
            return utils.tool_result({"ok": True, "workspace_id": workspace_id,
                                      "user_id": target_user_id, "status": m["status"]})
        except WorkspaceError as e:
            return utils.error_result(f"审批成员失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"审批成员失败: {e}")

    async def workspace_reject_member(self, workspace_id: str, target_user_id: str,
                                      ctx: Context | None = None,
                                      user_id: str | None = None) -> dict:
        """owner 拒绝成员加入申请。仅 owner 可操作。"""
        try:
            uid, _ = await self._identity(ctx, user_id)
            m = await self.workspace.reject_member(workspace_id, target_user_id, uid)
            return utils.tool_result({"ok": True, "workspace_id": workspace_id,
                                      "user_id": target_user_id, "status": m["status"]})
        except WorkspaceError as e:
            return utils.error_result(f"拒绝成员失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"拒绝成员失败: {e}")

    async def workspace_list(self, ctx: Context | None = None,
                             user_id: str | None = None) -> dict:
        """我的 workspace 列表（本人创建或已批准加入的）。"""
        try:
            uid, _ = await self._identity(ctx, user_id)
            workspaces = await self.workspace.list(uid)
            return utils.tool_result({"ok": True, "user_id": uid,
                                      "count": len(workspaces), "workspaces": workspaces})
        except WorkspaceError as e:
            return utils.error_result(f"获取工作区列表失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"获取工作区列表失败: {e}")

    async def workspace_get(self, workspace_id: str,
                            ctx: Context | None = None,
                            user_id: str | None = None) -> dict:
        """获取 workspace 详情（仅本人 workspace 可查）。"""
        try:
            uid, _ = await self._identity(ctx, user_id)
            ws = await self.workspace.get(workspace_id, uid)
            return utils.tool_result({"ok": True, "workspace": ws})
        except WorkspaceError as e:
            return utils.error_result(f"获取工作区失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"获取工作区失败: {e}")

    async def workspace_members(self, workspace_id: str,
                                ctx: Context | None = None,
                                user_id: str | None = None) -> dict:
        """成员列表（仅本人 workspace 可查）。"""
        try:
            uid, _ = await self._identity(ctx, user_id)
            members = await self.workspace.members(workspace_id, uid)
            return utils.tool_result({"ok": True, "workspace_id": workspace_id,
                                      "members": members})
        except WorkspaceError as e:
            return utils.error_result(f"获取成员列表失败: {e}")
        except Exception as e:  # noqa: BLE001
            return utils.error_result(f"获取成员列表失败: {e}")


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
    "requirement.archive": "归档需求：仅已审核完结（accepted/rejected/merged）可归档，移出活跃视图，可恢复",
    "requirement.restore": "恢复已归档需求：清除归档标记，保留状态/版本/审核历史",
    "requirement.find_similar": "查找与给定文本相似的历史反馈（查重）",
    "requirement.search": "查询需求列表，支持按状态/优先级/模块过滤，include_archived 含已归档",
    "requirement.get": "按 id 获取需求完整信息",
    "report.generate_html": "生成 HTML 分析报告，返回本地可下载路径",
    "report.generate_excel": "生成 Excel 报表（需求清单/反馈明细/聚类），返回本地可下载路径",
    "domain.stats": "数据域统计：feedback/requirement 数量与后端信息",
    "workspace.create": "创建产品 workspace，创建者自动成为 owner，自动生成工作区通行证（passcode，仅 owner 可见）",
    "workspace.join": "申请加入 workspace（pending，等待 owner 审批）",
    "workspace.join_by_passcode": "凭工作区通行证直接加入（校验通过即批准为 member，无需 owner 审批；通行证由 owner 通过 workspace.get 获取）",
    "workspace.approve_member": "owner 审批通过成员加入申请",
    "workspace.reject_member": "owner 拒绝成员加入申请",
    "workspace.list": "我的 workspace 列表（本人创建或已批准加入的）",
    "workspace.get": "获取 workspace 详情（仅本人 workspace 可查，passcode 仅 owner 可见）",
    "workspace.members": "成员列表（仅本人 workspace 可查）",
}
