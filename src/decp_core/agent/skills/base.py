"""Skill 基类：数字员工能力单元。

Skill 只依赖 ToolBackend 接口，不感知存储/MCP server 细节；
通过自然语言描述声明自己的触发场景，供 agent 调度器按意图路由。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from decp_core.agent.backends import ToolBackend


class BaseSkill(ABC):
    """技能基类。

    子类声明：
    - name / description：技能身份与触发描述（LLM 调度依据）
    - tools_required：依赖的 MCP 工具名列表（启动时校验后端可用性）
    - run()：技能主入口
    """

    name: str = "base"
    description: str = ""
    tools_required: list[str] = []

    def __init__(self, backend: ToolBackend) -> None:
        self.backend = backend

    async def validate(self) -> list[str]:
        """校验后端是否提供所需工具，返回缺失工具名列表。"""
        try:
            tools = await self.backend.list_tools()
            names = {t["name"] for t in tools}
        except Exception:  # noqa: BLE001
            names = set()
        missing = [t for t in self.tools_required if t not in names]
        return missing

    async def _call(self, name: str, **kwargs: Any) -> Any:
        """调用工具并解包结果：统一返回结构化内容 dict。

        - direct 后端 / MCP 高层 call_tool 返回 CallToolResult（structured_content）
        - 标准 MCP client 返回 CallToolResult（content 文本 JSON）
        """
        result = await self.backend.call(name, kwargs)
        return _unpack(result)


def _unpack(result: Any) -> Any:
    """把工具调用结果解包为可直接消费的数据结构。"""
    if hasattr(result, "structured_content") and result.structured_content is not None:
        return result.structured_content
    if hasattr(result, "content") and result.content:
        for c in result.content:
            if getattr(c, "type", "") == "text" and c.text:
                try:
                    import json

                    return json.loads(c.text)
                except (TypeError, ValueError):
                    return c.text
    return result

    @abstractmethod
    async def run(self, **params: Any) -> Any:
        """执行技能逻辑。"""
        raise NotImplementedError
