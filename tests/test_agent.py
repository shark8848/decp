"""Skill 层测试：direct 模式完整闭环 + 意图路由 + 双后端一致性。"""
from __future__ import annotations

import pytest

from decp_core.agent import DigitalEmployee
from decp_core.agent.backends import DirectBackend, build_backend, connect_backend
from decp_core.agent.registry import create_registry
from decp_core.mcp_.tools import DecpTools


def _make_agent(storage, reports_dir):
    tools = DecpTools(storage, reports_dir)
    backend = DirectBackend(tools)
    reg = create_registry(backend)
    return DigitalEmployee(backend=backend, registry=reg)


@pytest.mark.asyncio
async def test_skill_registry(sqlite_storage):
    agent = _make_agent(sqlite_storage, str(sqlite_storage._path.parent / "reports"))
    assert set(agent._registry.names()) == {"requirement_analysis", "query", "feedback_collect"}
    missing = await agent._registry.validate_all()
    assert missing == {}


def test_route():
    emp = DigitalEmployee()
    assert emp.route("收集反馈并分析，生成需求草稿") == "requirement_analysis"
    assert emp.route("查看最近的反馈") == "query"
    assert emp.route("生成报告") == "requirement_analysis"
    assert emp.route("录入反馈") == "feedback_collect"
    # 「录入一条客户反馈」同时含「录入/客户反馈/反馈」，不得被 requirement_analysis 抢占
    assert emp.route("录入一条客户反馈") == "feedback_collect"
    assert emp.route("登记客户反馈") == "feedback_collect"


@pytest.mark.asyncio
async def test_full_loop_direct(sqlite_storage):
    agent = _make_agent(sqlite_storage, str(sqlite_storage._path.parent / "reports"))
    res = await agent.execute(
        "收集反馈并分析，生成需求草稿",
        submit_feedback="客户 A 导入超过 5000 条订单时失败，影响月度结算。",
        customer="Customer A", module="批量订单导入",
    )
    assert res["skill"] == "requirement_analysis"
    result = res["result"]
    assert result["submitted"]["ok"] is True
    draft = result["draft"]["requirement"]
    assert draft["status"] == "draft"
    assert result["report_html"]["path"].endswith(".html")
    assert result["report_excel"]["path"].endswith(".xlsx")


@pytest.mark.asyncio
async def test_query_skill(sqlite_storage):
    agent = _make_agent(sqlite_storage, str(sqlite_storage._path.parent / "reports"))
    res = await agent.execute("查看最近的反馈")
    assert res["skill"] == "query"
    assert "feedbacks" in res["result"]
    assert "requirements" in res["result"]


@pytest.mark.asyncio
async def test_skill_review(sqlite_storage):
    agent = _make_agent(sqlite_storage, str(sqlite_storage._path.parent / "reports"))
    # 先建立草稿
    res = await agent.execute("收集反馈并分析，生成需求草稿", submit_feedback="客户 C 无法登录系统。")
    rid = res["result"]["draft"]["requirement"]["id"]
    # 审核
    rv = await agent.run_skill("requirement_analysis", requirement_id=rid, decision="accept", reviewer="pm-zhang")
    status = rv["result"]["review"]["requirement"]["status"]
    assert status == "accepted"


@pytest.mark.asyncio
async def test_build_backend_direct(sqlite_storage):
    """build_backend(direct) 能基于配置构建并可调用。"""
    from decp_core.config import Settings

    s = Settings(storage_backend="sqlite", sqlite_path=str(sqlite_storage._path),
                 reports_dir=str(sqlite_storage._path.parent / "reports"))
    backend = build_backend("direct", s)
    await connect_backend(backend)
    names = await backend.list_tools()
    assert any(n["name"] == "feedback.submit" for n in names)
    await backend.close()
