# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""MCP 工具统一响应构造：结构化内容 + 文本摘要。

工具函数直接返回 mcp.types.CallToolResult 实例——
MCPServer.convert_result 对 CallToolResult 原样透传（保留 structured_content），
MCP client 端（真实 agent 场景）与进程内直调（Skill direct 模式）行为一致。
"""
from __future__ import annotations

import json
from typing import Any

from mcp.types import CallToolResult, TextContent


def tool_result(data: Any, *, is_error: bool = False) -> CallToolResult:
    """构造标准工具返回。

    - content: 人类/LLM 可读的文本摘要（JSON）
    - structured_content: 结构化数据（程序可解析）
    """
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        is_error=is_error,
        structured_content=data,
    )


def error_result(message: str) -> CallToolResult:
    return tool_result({"error": message}, is_error=True)
