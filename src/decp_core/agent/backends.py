# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""数字员工 Skill 层的工具调用双模式后端。

- direct: 进程内直调 MCP 已注册的 tool 函数（同一服务进程，测试/演示/单进程部署）
- client: 通过 mcp client（stdio）连接独立运行的 decp-mcp server（真实 agent-MCP 部署形态）

两种后端暴露同一 async 接口，Skill 代码无感知切换。
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
from contextlib import AsyncExitStack
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client


class ToolBackend(Protocol):
    """技能调工具的统一接口。"""

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any: ...

    async def list_tools(self) -> list[dict[str, Any]]: ...

    async def close(self) -> None: ...


def _text_of(result: types.CallToolResult) -> str:
    parts = []
    for c in result.content or []:
        if getattr(c, "type", "") == "text":
            parts.append(c.text)
    return "\n".join(parts)


class DirectBackend:
    """进程内直调：直接执行 MCP 层已注册的工具函数。"""

    def __init__(self, tools: Any) -> None:
        self._tools = tools
        # 使用 DecpTools 统一工具名映射（与 MCP 层一致）
        self._names = list(tools.TOOL_BINDINGS.keys())

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        fn = self._tools.tool_callable(name)
        if fn is None:
            raise KeyError(f"工具不存在: {name}")
        return await fn(**(arguments or {}))

    async def list_tools(self) -> list[dict[str, Any]]:
        from decp_core.mcp_.tools import _TOOL_DESCS

        return [{"name": n, "desc": _TOOL_DESCS.get(n, "")} for n in self._names]

    async def close(self) -> None:
        return None


class ClientBackend:
    """跨进程：mcp client 通过 stdio 连接独立 server 进程。"""

    def __init__(self, command: list[str], cwd: str | None = None) -> None:
        self._command = command
        self._cwd = cwd
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> ClientSession:
        if self._session is not None:
            return self._session
        async with self._lock:
            if self._session is not None:
                return self._session
            params = StdioServerParameters(command=self._command[0], args=self._command[1:], cwd=self._cwd)
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
            return self._session

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        session = await self._ensure()
        result = await session.call_tool(name, arguments)
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        session = await self._ensure()
        res = await session.list_tools()
        return [{"name": t.name, "desc": t.description or ""} for t in res.tools]

    async def close(self) -> None:
        if self._session is not None:
            await self._stack.aclose()
            self._session = None


def build_backend(mode: str, settings: Any = None) -> ToolBackend:
    """按配置创建工具调用后端。

    - mode == "direct": 创建/复用进程内 storage + tools，直调函数
    - mode == "client": 通过 mcp client 连接独立 server
    """
    if mode == "direct":
        from decp_core.config import settings as _settings
        from decp_core.storage import create_storage

        s = settings or _settings
        storage = create_storage(s)
        return DirectBackend(_lazy_tools(storage, s))
    if mode == "client":
        from decp_core.config import settings as _settings

        s = settings or _settings
        return ClientBackend(s.skill_mcp_command, cwd=s.skill_mcp_cwd)
    raise ValueError(f"未知工具后端: {mode}")


def _lazy_tools(storage: Any, s: Any) -> Any:
    """direct 模式：直接构建 DecpTools（不经过 MCP server，省去协议层）。"""
    from decp_core.mcp_.tools import DecpTools

    return DecpTools(storage, s.reports_dir)


async def connect_backend(backend: ToolBackend) -> None:
    """初始化 direct 后端所需的存储。"""
    if isinstance(backend, DirectBackend):
        storage = backend._tools.storage
        await storage.connect()
        await storage.init_schema()
