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


@pytest.mark.asyncio
async def test_workspace_passcode_create_and_join(sqlite_storage):
    """创建生成通行证；持有者可凭通行证直通加入（approved，无需 owner 审批）。"""
    ws_svc = WorkspaceService(sqlite_storage)
    w = await ws_svc.create("通行证产品", "alice")
    passcode = w.get("passcode")
    assert passcode and passcode.startswith("DECP-")
    assert len(passcode) > 8  # 高熵，防枚举

    # bob 持通行证加入 → 直接 approved
    m = await ws_svc.join_by_passcode(w["id"], passcode, "bob")
    assert m["status"] == "approved"
    assert m["role"] == "member"
    stored = await sqlite_storage.member_get(w["id"], "bob")
    assert stored["status"] == "approved"
    # bob 获得数据访问权
    await ws_svc.assert_member(w["id"], "bob")


@pytest.mark.asyncio
async def test_workspace_passcode_wrong_rejected(sqlite_storage):
    """错误通行证被拒绝，不产生成员记录。"""
    ws_svc = WorkspaceService(sqlite_storage)
    w = await ws_svc.create("通行证产品2", "alice")
    with pytest.raises(WorkspaceError):
        await ws_svc.join_by_passcode(w["id"], "DECP-WRONG-0000-0000", "bob")
    assert await sqlite_storage.member_get(w["id"], "bob") is None
    # 空/缺失通行证也被拒
    with pytest.raises(WorkspaceError):
        await ws_svc.join_by_passcode(w["id"], "", "bob")
    # 通行证对不存在的 workspace 无效
    with pytest.raises(WorkspaceError):
        await ws_svc.join_by_passcode("ws-nonexist", "DECP-0000-0000-0000", "bob")


@pytest.mark.asyncio
async def test_workspace_passcode_hidden_from_member(sqlite_storage):
    """passcode 仅 owner 可见；非 owner 查询被脱敏。"""
    ws_svc = WorkspaceService(sqlite_storage)
    w = await ws_svc.create("通行证产品3", "alice")
    passcode = w["passcode"]
    # owner 可见
    got = await ws_svc.get(w["id"], "alice")
    assert got["passcode"] == passcode
    assert any(x["passcode"] == passcode for x in await ws_svc.list("alice"))
    # bob 凭通行证加入后，详情/列表均看不到 passcode
    await ws_svc.join_by_passcode(w["id"], passcode, "bob")
    got_bob = await ws_svc.get(w["id"], "bob")
    assert "passcode" not in got_bob
    bob_list = [x for x in await ws_svc.list("bob") if x["id"] == w["id"]]
    assert bob_list and "passcode" not in bob_list[0]


@pytest.mark.asyncio
async def test_workspace_passcode_via_mcp(sqlite_storage):
    """MCP 层：创建返回通行证 → 另一用户凭通行证直通加入 → 获得数据访问权。"""
    server = MCPServer("decp", version="0.1.0")
    register_all_tools(server, sqlite_storage, str(sqlite_storage._path.parent / "reports"))

    r = await server.call_tool("workspace.create", {"name": "MCP 通行证产品", "user_id": "alice"})
    ws_id = r.structured_content["workspace"]["id"]
    passcode = r.structured_content["workspace"]["passcode"]
    assert passcode

    # bob 无通行证时加入失败
    denied = await server.call_tool("workspace.join_by_passcode", {
        "workspace_id": ws_id, "passcode": "DECP-WRONG-0000-0000", "user_id": "bob",
    })
    assert "error" in denied.structured_content
    assert "通行证" in denied.structured_content["error"]

    # bob 凭正确通行证加入 → approved，可直接写
    ok = await server.call_tool("workspace.join_by_passcode", {
        "workspace_id": ws_id, "passcode": passcode, "user_id": "bob",
    })
    assert ok.structured_content["ok"] is True
    assert ok.structured_content["status"] == "approved"
    fb = await server.call_tool("feedback.submit", {
        "content": "bob 凭通行证加入后的写入", "user_id": "bob", "workspace_id": ws_id,
    })
    assert fb.structured_content["ok"] is True

    # bob 通过 workspace.get 查看详情，passcode 被脱敏
    g = await server.call_tool("workspace.get", {"workspace_id": ws_id, "user_id": "bob"})
    assert "passcode" not in g.structured_content["workspace"]
    # alice 是 owner，能看到
    g2 = await server.call_tool("workspace.get", {"workspace_id": ws_id, "user_id": "alice"})
    assert g2.structured_content["workspace"]["passcode"] == passcode
