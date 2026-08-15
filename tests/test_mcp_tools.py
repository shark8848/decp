"""MCP 工具层测试：13 个工具注册与调用、结构化返回。"""
from __future__ import annotations

import pytest
from mcp.server.mcpserver import MCPServer

from decp_core.mcp_.tools import DecpTools, register_all_tools


@pytest.mark.asyncio
async def test_register_all_tools(sqlite_storage):
    server = MCPServer("decp", version="0.1.0")
    tools = register_all_tools(server, sqlite_storage, str(sqlite_storage._path.parent / "reports"))
    listed = await server.list_tools()
    names = [t.name for t in listed]
    assert len(names) == len(DecpTools.TOOL_BINDINGS) == 13
    expected = {
        "feedback.submit", "feedback.search", "feedback.get",
        "requirement.analyze", "requirement.generate_draft", "requirement.create",
        "requirement.review", "requirement.find_similar", "requirement.search",
        "requirement.get", "report.generate_html", "report.generate_excel",
        "domain.stats",
    }
    assert set(names) == expected
    assert tools.tool_callable("feedback.submit") is not None


@pytest.mark.asyncio
async def test_feedback_submit_search(sqlite_storage):
    server = MCPServer("decp", version="0.1.0")
    register_all_tools(server, sqlite_storage, str(sqlite_storage._path.parent / "reports"))
    r = await server.call_tool("feedback.submit", {
        "content": "客户 A 导入超过 5000 条订单时失败，影响月度结算。",
        "customer": "Customer A", "module": "批量订单导入",
    })
    assert r.structured_content["ok"] is True
    fid = r.structured_content["id"]
    assert fid.startswith("fb-")

    s = await server.call_tool("feedback.search", {"customer": "Customer A"})
    items = s.structured_content["items"]
    assert any(i["id"] == fid for i in items)


@pytest.mark.asyncio
async def test_requirement_flow(sqlite_storage):
    server = MCPServer("decp", version="0.1.0")
    register_all_tools(server, sqlite_storage, str(sqlite_storage._path.parent / "reports"))
    await server.call_tool("feedback.submit", {
        "content": "客户 B 在 ERP 同步时报错，无法完成月度对账。", "customer": "Customer B",
    })
    analysis = await server.call_tool("requirement.analyze", {})
    assert "categories" in analysis.structured_content

    draft = await server.call_tool("requirement.generate_draft", {})
    req = draft.structured_content["requirement"]
    assert req["status"] == "draft"

    review = await server.call_tool("requirement.review", {
        "requirement_id": req["id"], "decision": "accept", "reviewer": "pm",
    })
    assert review.structured_content["requirement"]["status"] == "accepted"


@pytest.mark.asyncio
async def test_find_similar(sqlite_storage):
    server = MCPServer("decp", version="0.1.0")
    register_all_tools(server, sqlite_storage, str(sqlite_storage._path.parent / "reports"))
    await server.call_tool("feedback.submit", {
        "content": "客户 A 导入超过 5000 条订单时失败，影响月度结算。",
    })
    r = await server.call_tool("requirement.find_similar", {"text": "客户导入超过5000条订单时失败"})
    assert r.structured_content["total"] >= 1


@pytest.mark.asyncio
async def test_report_generation(sqlite_storage):
    server = MCPServer("decp", version="0.1.0")
    register_all_tools(server, sqlite_storage, str(sqlite_storage._path.parent / "reports"))
    await server.call_tool("feedback.submit", {"content": "客户 A 导入订单失败，影响结算。"})
    html = await server.call_tool("report.generate_html", {})
    assert html.structured_content["ok"] is True
    assert html.structured_content["path"].endswith(".html")
    excel = await server.call_tool("report.generate_excel", {})
    assert excel.structured_content["path"].endswith(".xlsx")


@pytest.mark.asyncio
async def test_domain_stats(sqlite_storage):
    server = MCPServer("decp", version="0.1.0")
    register_all_tools(server, sqlite_storage, str(sqlite_storage._path.parent / "reports"))
    await server.call_tool("feedback.submit", {"content": "测试反馈"})
    stats = await server.call_tool("domain.stats", {})
    assert stats.structured_content["feedback"] == 1
    assert stats.structured_content["backend"] == "sqlite"
