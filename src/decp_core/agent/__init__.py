# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""数字员工 Agent：面向自然语言指令的技能调度入口。

产品经理 / 需求收集人员通过自然语言指令与数字员工交互：
「收集反馈并分析，生成需求草稿」→ requirement_analysis
「查看最近的反馈」→ query
「生成报告」→ requirement_analysis（report 动作）

当前使用基于关键词+描述匹配的确定性路由；接入 LLM 时可将 skill.description
作为工具描述交给模型选择，接口保持一致。
"""
from __future__ import annotations

import re
from typing import Any

from decp_core.agent.backends import ToolBackend, build_backend, connect_backend
from decp_core.agent.registry import SkillRegistry, create_registry
from decp_core.logging_setup import get_decp_logger

logger = get_decp_logger("agent")

# 意图 → 技能路由关键词
_INTENT_KEYWORDS: dict[str, tuple[str, list[str]]] = {
    # (skill_name, [触发关键词])
    "requirement_analysis": ("requirement_analysis", [
        "收集", "反馈", "分析", "整理", "需求", "草稿", "生成", "报告", "闭环",
    ]),
    "query": ("query", [
        "查看", "查询", "搜索", "找", "情况", "状态", "列表", "最近",
    ]),
    "feedback_collect": ("feedback_collect", [
        "录入反馈", "提交反馈", "登记反馈", "上报反馈", "新增反馈", "客户反馈", "录入", "登记",
    ]),
}


class DigitalEmployee:
    """数字员工（Agent Runtime 层门面）。"""

    def __init__(self, backend: ToolBackend | None = None, registry: SkillRegistry | None = None) -> None:
        self._backend = backend
        self._registry = registry

    # ---- 生命周期 ----
    @classmethod
    async def create(cls, *, mode: str | None = None, settings: Any = None) -> "DigitalEmployee":
        """异步工厂：创建后端 + 技能注册表。

        mode 覆盖配置 skill_tool_backend（direct / client）。
        """
        from decp_core.config import settings as _default_settings

        s = settings or _default_settings
        mode = mode or s.skill_tool_backend
        backend = build_backend(mode, s)
        await connect_backend(backend)
        reg = create_registry(backend)
        missing = await reg.validate_all()
        if missing:
            logger.warning("技能工具依赖缺失: %s", missing)
        return cls(backend=backend, registry=reg)

    async def close(self) -> None:
        if self._backend is not None:
            await self._backend.close()

    # ---- 技能调度 ----
    def skills(self) -> list[dict[str, str]]:
        """列出数字员工可用技能及其触发描述。"""
        return [
            {"name": s.name, "description": s.description, "tools": s.tools_required}
            for s in self._registry.all()
        ]

    def route(self, instruction: str) -> str:
        """意图路由：把自然语言指令映射到技能名（确定性关键词匹配）。

        更长（更具体）的关键词权重更高；精确短语（如「录入反馈」）优先于单字命中。
        """
        text = instruction.lower()
        best, best_score = "query", 0
        for skill, (_, kws) in _INTENT_KEYWORDS.items():
            # 长关键词（精确短语）权重 2，短关键词权重 1
            score = sum(2 if len(k) >= 3 else 1 for k in kws if k in text)
            if score > best_score:
                best, best_score = skill, score
        return best

    async def execute(self, instruction: str, **params: Any) -> dict[str, Any]:
        """执行自然语言指令：路由到技能并运行。

        返回 {skill, matched_by, result, instruction}。
        """
        skill_name = self.route(instruction)
        skill = self._registry.get(skill_name)
        if skill is None:
            return {"error": f"技能不存在: {skill_name}"}
        result = await skill.run(**params)
        return {
            "skill": skill_name,
            "matched_by": instruction,
            "result": result,
        }

    async def run_skill(self, name: str, **params: Any) -> dict[str, Any]:
        """按技能名直接执行（跳过意图路由）。"""
        skill = self._registry.get(name)
        if skill is None:
            return {"error": f"技能不存在: {name}，可用: {self._registry.names()}"}
        result = await skill.run(**params)
        return {"skill": name, "result": result}
