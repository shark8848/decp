# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""MCP server 入口：装配存储、注册工具、启动传输。

用法：
- stdio:  python -m decp_core.mcp_.main
- streamable http: python -m decp_core.mcp_.main --transport http --port 18100
"""
from __future__ import annotations

import argparse
import asyncio

from mcp.server.mcpserver import MCPServer

from decp_core.config import settings
from decp_core.logging_setup import (
    configure_logging,
    extract_trace_from_headers,
    get_decp_logger,
    set_trace_id,
)
from decp_core.mcp_.tools import register_all_tools
from decp_core.storage import create_storage


def build_server(*, reports_dir: str | None = None) -> tuple[MCPServer, object]:
    """装配 MCP server：初始化存储 → 注册工具。返回 (server, storage)。"""
    storage = create_storage(settings)
    server = MCPServer(
        "decp",
        version="0.1.0",
        title="DECP 产品需求分析",
        description=(
            "DECP · 联邦数字员工协作平台：产品需求收集、整理与分析。"
            "通过 feedback / requirement / report 数据域工具，将分散的客户反馈"
            "转换为结构化、去重、可追溯、可审核的需求，供产品经理审核入库。"
        ),
    )
    register_all_tools(server, storage, reports_dir or settings.reports_dir)
    return server, storage


async def _amain(transport: str, port: int) -> None:
    # 统一在两条入口（run_stdio / main）前完成日志装配：SDK 远程上报必须
    # 在首个日志调用之前挂载 HttpLogHandler，否则日志中心收不到上报。
    configure_logging(module_name="decp", level=settings.log_level, settings=settings)
    server, storage = build_server()
    try:
        await storage.connect()
        await storage.init_schema()
        get_decp_logger("mcp").info("storage ready: %s", settings.storage_backend)
        if transport == "stdio":
            await server.run_stdio_async()
        elif transport == "http":
            from starlette.middleware.base import BaseHTTPMiddleware

            app = server.streamable_http_app(host="0.0.0.0")

            class TraceContextMiddleware(BaseHTTPMiddleware):
                """请求级 trace：提取上游透传的 x-trace-id 等 header，绑定到 contextvar。

                使该请求产生的所有日志（含 Service 层业务打点）共享同一
                trace_id，日志中心可按 trace 关联整条调用链。
                """

                async def dispatch(self, request, call_next):
                    tid = extract_trace_from_headers(request.headers)
                    if tid:
                        set_trace_id(tid)
                    return await call_next(request)

            app.add_middleware(TraceContextMiddleware)

            import uvicorn  # type: ignore[import-not-found]

            config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level=settings.log_level.lower())
            server_ = uvicorn.Server(config)
            await server_.serve()
        else:
            raise ValueError(f"未知传输: {transport}")
    finally:
        await storage.close()


def run_stdio() -> None:
    asyncio.run(_amain("stdio", 0))


def main() -> None:
    parser = argparse.ArgumentParser(description="DECP MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--port", type=int, default=18100)
    args = parser.parse_args()
    asyncio.run(_amain(args.transport, args.port))


if __name__ == "__main__":
    main()
