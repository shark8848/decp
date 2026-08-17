"""工作区多租户隔离测试：WorkspaceService 权限 + MCP 工具跨工作区数据隔离。"""
from __future__ import annotations

import pytest
from mcp.server.mcpserver import MCPServer

from decp_core.mcp_.tools import register_all_tools
from decp_core.services import WorkspaceError, WorkspaceService


@pytest.mark.asyncio
async def test_workspace_service_full_flow(sqlite_storage):
    ws = WorkspaceService(sqlite_storage)

    # 1. 任意用户可创建 workspace，owner 自动成为已批准成员
    w = await ws.create("AI 产品", "alice")
    assert w["owner_user_id"] == "alice"
    owner_member = await sqlite_storage.member_get(w["id"], "alice")
    assert owner_member["role"] == "owner"
    assert owner_member["status"] == "approved"

    # 2. 他人申请加入 → pending
    m = await ws.join(w["id"], "bob")
    assert m["status"] == "pending"

    # 3. 非 owner 无权审批
    with pytest.raises(WorkspaceError):
        await ws.approve_member(w["id"], "bob", "bob")

    # 4. owner 审批通过 → approved + joined_at 盖章
    m2 = await ws.approve_member(w["id"], "bob", "alice")
    assert m2["status"] == "approved"
    assert m2["joined_at"] is not None

    # 5. 拒绝流程
    await ws.join(w["id"], "carol")
    await ws.reject_member(w["id"], "carol", "alice")
    assert (await sqlite_storage.member_get(w["id"], "carol"))["status"] == "rejected"

    # 6. 成员列表
    members = await ws.members(w["id"], "alice")
    by_user = {x["user_id"]: x for x in members}
    assert by_user["alice"]["role"] == "owner"
    assert by_user["bob"]["status"] == "approved"
    assert by_user["carol"]["status"] == "rejected"

    # 7. 列表/详情/资格校验
    assert {x["id"] for x in await ws.list("alice")} == {w["id"]}
    assert {x["id"] for x in await ws.list("bob")} == {w["id"]}
    assert await ws.list("nobody") == []
    await ws.assert_member(w["id"], "bob")
    with pytest.raises(WorkspaceError):
        await ws.assert_member(w["id"], "carol")
    with pytest.raises(WorkspaceError):
        await ws.assert_member(w["id"], "eve")


@pytest.mark.asyncio
async def test_default_workspace_compat(sqlite_storage):
    """存量兼容：默认工作区对默认用户可用（单租户数据零破坏）。"""
    ws = WorkspaceService(sqlite_storage)
    await ws.ensure_default()
    member = await sqlite_storage.member_get("default", "default_user")
    assert member is not None
    assert member["role"] == "owner"
    assert member["status"] == "approved"
    # 幂等
    await ws.ensure_default()
    assert await sqlite_storage.workspace_get("default") is not None


@pytest.mark.asyncio
async def test_workspace_data_isolation_via_mcp(sqlite_storage):
    """MCP 工具级跨工作区隔离：非成员读不到、写不到别的 workspace。"""
    server = MCPServer("decp", version="0.1.0")
    register_all_tools(server, sqlite_storage, str(sqlite_storage._path.parent / "reports"))

    # alice 建 workspace，提交反馈
    r = await server.call_tool("workspace.create", {"name": "AI 产品", "user_id": "alice"})
    ws = r.structured_content["workspace"]
    ws_id = ws["id"]

    fb = await server.call_tool("feedback.submit", {
        "content": "客户 A 导入超过 5000 条订单时失败，影响月度结算。",
        "customer": "Customer A", "module": "批量订单导入",
        "user_id": "alice", "workspace_id": ws_id,
    })
    assert fb.structured_content["ok"] is True
    fid = fb.structured_content["id"]

    # bob 未申请 → 非成员，写被拒
    denied = await server.call_tool("feedback.submit", {
        "content": "bob 试图写入",
        "user_id": "bob", "workspace_id": ws_id,
    })
    assert denied.structured_content.get("ok") is not True
    assert "成员" in denied.structured_content.get("error", "")

    # bob 读不到（非成员，查询被拒）
    search = await server.call_tool("feedback.search", {
        "user_id": "bob", "workspace_id": ws_id,
    })
    assert "error" in search.structured_content
    assert "成员" in search.structured_content["error"]

    # alice 能读到自己的
    search2 = await server.call_tool("feedback.search", {
        "user_id": "alice", "workspace_id": ws_id,
    })
    assert search2.structured_content["count"] == 1
    assert search2.structured_content["items"][0]["id"] == fid


@pytest.mark.asyncio
async def test_workspace_join_approve_then_access(sqlite_storage):
    """bob 申请 → alice 审批 → bob 获得读写权限，且数据隔离仍生效。"""
    server = MCPServer("decp", version="0.1.0")
    register_all_tools(server, sqlite_storage, str(sqlite_storage._path.parent / "reports"))

    r = await server.call_tool("workspace.create", {"name": "ERP", "user_id": "alice"})
    ws_id = r.structured_content["workspace"]["id"]

    # bob 申请加入
    j = await server.call_tool("workspace.join", {"workspace_id": ws_id, "user_id": "bob"})
    assert j.structured_content["status"] == "pending"

    # bob 在批准前不能写
    denied = await server.call_tool("feedback.submit", {
        "content": "bob 批准前写入", "user_id": "bob", "workspace_id": ws_id,
    })
    assert "成员" in denied.structured_content.get("error", "")

    # alice 审批 bob
    a = await server.call_tool("workspace.approve_member", {
        "workspace_id": ws_id, "target_user_id": "bob", "user_id": "alice",
    })
    assert a.structured_content["status"] == "approved"

    # bob 批准后能写
    ok = await server.call_tool("feedback.submit", {
        "content": "bob 批准后写入", "user_id": "bob", "workspace_id": ws_id,
    })
    assert ok.structured_content["ok"] is True

    # 但 bob 读不到 alice 在另一个 workspace 的数据
    r2 = await server.call_tool("workspace.create", {"name": "另一个产品", "user_id": "alice"})
    ws2 = r2.structured_content["workspace"]["id"]
    await server.call_tool("feedback.submit", {
        "content": "alice 在 ws2 的数据", "user_id": "alice", "workspace_id": ws2,
    })
    s = await server.call_tool("feedback.search", {
        "user_id": "bob", "workspace_id": ws2,
    })
    assert "error" in s.structured_content
    assert "成员" in s.structured_content["error"]
    # bob 在自己 workspace 能看到自己的
    s2 = await server.call_tool("feedback.search", {
        "user_id": "bob", "workspace_id": ws_id,
    })
    assert s2.structured_content["count"] == 1
    # domain.stats 按 workspace 口径统计，互不泄漏
    st1 = await server.call_tool("domain.stats", {"user_id": "bob", "workspace_id": ws_id})
    assert st1.structured_content["feedback"] == 1
    st2 = await server.call_tool("domain.stats", {"user_id": "alice", "workspace_id": ws2})
    assert st2.structured_content["feedback"] == 1
