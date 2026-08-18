"""缺陷数据域测试：状态机、严重级、多域关联、反馈转缺陷、归档。"""
from __future__ import annotations

import pytest

from decp_core.models import Bug, BugCreate, TaskCreate
from decp_core.services import BugService, TaskService


@pytest.mark.asyncio
async def test_bug_create_and_get(sqlite_storage):
    svc = BugService(sqlite_storage)
    b = await svc.create(BugCreate(title="登录报错", severity="high", channel="feedback",
                                   reproduce_steps="1.打开登录页 2.输入密码 3.崩溃"),
                         workspace_id="default")
    assert b.id.startswith("bg-")
    assert b.status == "new"
    assert b.severity == "high"
    assert b.channel == "feedback"
    got = await svc.get(b.id, workspace_id="default")
    assert got is not None and got.title == "登录报错"
    # 隔离
    assert await svc.get(b.id, workspace_id="other") is None


@pytest.mark.asyncio
async def test_bug_state_machine_full_path(sqlite_storage):
    svc = BugService(sqlite_storage)
    b = await svc.create(BugCreate(title="缺陷"), workspace_id="default")
    # new → confirmed → in_progress → fixed → verified → closed
    b = await svc.transition(b.id, "confirmed", actor="alice", workspace_id="default")
    assert b.status == "confirmed"
    b = await svc.transition(b.id, "in_progress", actor="alice", workspace_id="default")
    assert b.status == "in_progress"
    b = await svc.transition(b.id, "fixed", actor="alice", workspace_id="default")
    assert b.status == "fixed"
    assert b.fixed_at is not None
    b = await svc.transition(b.id, "verified", actor="tester", workspace_id="default")
    assert b.status == "verified"
    b = await svc.transition(b.id, "closed", actor="tester", workspace_id="default")
    assert b.status == "closed"
    assert b.closed_at is not None


@pytest.mark.asyncio
async def test_bug_illegal_transition_rejected(sqlite_storage):
    svc = BugService(sqlite_storage)
    b = await svc.create(BugCreate(title="缺陷"), workspace_id="default")
    # new 不能直接 fixed
    with pytest.raises(ValueError, match="非法状态流转"):
        await svc.transition(b.id, "fixed", actor="alice", workspace_id="default")


@pytest.mark.asyncio
async def test_bug_wonfix_requires_comment(sqlite_storage):
    svc = BugService(sqlite_storage)
    b = await svc.create(BugCreate(title="缺陷"), workspace_id="default")
    with pytest.raises(ValueError, match="wonfix"):
        await svc.transition(b.id, "wonfix", actor="alice", workspace_id="default")
    b2 = await svc.transition(b.id, "wonfix", actor="alice", comment="设计如此，暂不修复",
                              workspace_id="default")
    assert b2.status == "wonfix"


@pytest.mark.asyncio
async def test_bug_reopen_from_verified(sqlite_storage):
    svc = BugService(sqlite_storage)
    b = await svc.create(BugCreate(title="缺陷"), workspace_id="default")
    for st in ("confirmed", "in_progress", "fixed", "verified"):
        b = await svc.transition(b.id, st, actor="alice", workspace_id="default")
    # verified → in_progress 允许 reopen
    b = await svc.transition(b.id, "in_progress", actor="alice", comment="回归发现新问题",
                             workspace_id="default")
    assert b.status == "in_progress"


@pytest.mark.asyncio
async def test_bug_create_with_task_ids_reverse_sync(sqlite_storage):
    """bug.create 带 task_ids 时反向同步 task.bug_ids（双向引用一致）。"""
    bug_svc = BugService(sqlite_storage)
    task_svc = TaskService(sqlite_storage)
    t = await task_svc.create(TaskCreate(title="修复任务"), workspace_id="default")
    b = await bug_svc.create(BugCreate(title="缺陷", task_ids=[t.id]), workspace_id="default")
    t2 = await task_svc.get(t.id, workspace_id="default")
    assert b.id in t2.bug_ids
    b2 = await bug_svc.get(b.id, workspace_id="default")
    assert t.id in b2.task_ids


@pytest.mark.asyncio
async def test_bug_link_multi_domain(sqlite_storage):
    bug_svc = BugService(sqlite_storage)
    task_svc = TaskService(sqlite_storage)
    b = await bug_svc.create(BugCreate(title="缺陷"), workspace_id="default")
    t = await task_svc.create(TaskCreate(title="修复任务"), workspace_id="default")
    b2 = await bug_svc.link(b.id, feedback_ids=["fb-1"], requirement_ids=["req-1"],
                            task_ids=[t.id], meeting_ids=["mt-1"], workspace_id="default")
    assert b2.feedback_ids == ["fb-1"]
    assert b2.requirement_ids == ["req-1"]
    assert b2.task_ids == [t.id]
    assert b2.meeting_ids == ["mt-1"]
    # 反向同步：task.bug_ids
    t2 = await task_svc.get(t.id, workspace_id="default")
    assert b.id in t2.bug_ids


@pytest.mark.asyncio
async def test_bug_from_feedback(sqlite_storage):
    from decp_core.models import FeedbackCreate
    from decp_core.services import FeedbackService
    fb_svc = FeedbackService(sqlite_storage)
    fb = await fb_svc.create(FeedbackCreate(content="系统崩溃无法使用", module="认证",
                                            customer="Customer A"), workspace_id="default")
    bug_svc = BugService(sqlite_storage)
    b = await bug_svc.from_feedback(fb.model_dump(), actor="analyst", workspace_id="default")
    assert b.channel == "feedback"
    assert fb.id in b.feedback_ids
    assert "系统崩溃" in b.title


@pytest.mark.asyncio
async def test_bug_search_filters(sqlite_storage):
    svc = BugService(sqlite_storage)
    await svc.create(BugCreate(title="高严重缺陷", severity="high", module="订单",
                               assignee=None), workspace_id="default")
    await svc.create(BugCreate(title="低严重缺陷", severity="low", module="报表"),
                     workspace_id="default")
    items = await svc.search(severity="high", workspace_id="default")
    assert len(items) == 1 and items[0].title == "高严重缺陷"
    items = await svc.search(module="报表", workspace_id="default")
    assert len(items) == 1 and items[0].title == "低严重缺陷"


@pytest.mark.asyncio
async def test_bug_archive_restore(sqlite_storage):
    svc = BugService(sqlite_storage)
    b = await svc.create(BugCreate(title="待归档缺陷"), workspace_id="default")
    b2 = await svc.archive(b.id, archived_by="alice", workspace_id="default")
    assert b2.archived is True
    assert await svc.search(workspace_id="default") == []
    items = await svc.search(include_archived=True, workspace_id="default")
    assert len(items) == 1
    b3 = await svc.restore(b.id, workspace_id="default")
    assert b3.archived is False


@pytest.mark.asyncio
async def test_bug_upload_plan(sqlite_storage):
    svc = BugService(sqlite_storage)
    b = await svc.create(BugCreate(title="缺陷"), workspace_id="default")
    b2 = await svc.upload_plan(b.id, "https://docs/fix.pdf", actor="alice", workspace_id="default")
    assert "https://docs/fix.pdf" in b2.plan_links
