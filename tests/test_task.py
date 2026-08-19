"""团队任务看板测试：task CRUD、看板流转、排期、方案链接、需求转任务、隔离。"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from decp_core.models import BugCreate, SprintCreate, Task, TaskCreate
from decp_core.services import BugService, SprintService, TaskService


def _dt(days=0):
    from datetime import timedelta
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=days)


@pytest.mark.asyncio
async def test_task_create_and_get(sqlite_storage):
    svc = TaskService(sqlite_storage)
    t = await svc.create(TaskCreate(title="重构报表模块", type="tech_debt", priority="P1"),
                         workspace_id="default")
    assert t.id.startswith("ts-")
    assert t.status == "backlog"
    assert t.type == "tech_debt"
    got = await svc.get(t.id, workspace_id="default")
    assert got is not None and got.title == "重构报表模块"
    # 隔离：其他 workspace 不可见
    assert await svc.get(t.id, workspace_id="other") is None


@pytest.mark.asyncio
async def test_task_create_rejects_non_member_assignee(sqlite_storage):
    svc = TaskService(sqlite_storage)
    with pytest.raises(ValueError, match="已批准成员"):
        await svc.create(TaskCreate(title="x", assignee="nobody"), workspace_id="default")


@pytest.mark.asyncio
async def test_task_move_status_flow_and_timestamps(sqlite_storage):
    svc = TaskService(sqlite_storage)
    t = await svc.create(TaskCreate(title="排期任务"), workspace_id="default")
    # backlog → in_progress 记 started_at
    t2 = await svc.move(t.id, "in_progress", actor="alice", workspace_id="default")
    assert t2.status == "in_progress"
    assert t2.started_at is not None
    # → review
    t3 = await svc.move(t.id, "review", actor="alice", workspace_id="default")
    assert t3.status == "review"
    # → done 记 done_at
    t4 = await svc.move(t.id, "done", actor="alice", workspace_id="default")
    assert t4.status == "done"
    assert t4.done_at is not None
    # 无效状态拒绝
    with pytest.raises(ValueError, match="未知任务状态"):
        await svc.move(t.id, "bogus", actor="alice", workspace_id="default")


@pytest.mark.asyncio
async def test_task_move_blocked_requires_comment(sqlite_storage):
    svc = TaskService(sqlite_storage)
    t = await svc.create(TaskCreate(title="需要阻塞的任务"), workspace_id="default")
    with pytest.raises(ValueError, match="阻塞原因"):
        await svc.move(t.id, "blocked", actor="alice", workspace_id="default")
    t2 = await svc.move(t.id, "blocked", actor="alice", comment="依赖未就绪", workspace_id="default")
    assert t2.status == "blocked"


@pytest.mark.asyncio
async def test_task_board_groups_by_column_and_sorts(sqlite_storage):
    svc = TaskService(sqlite_storage)
    await svc.create(TaskCreate(title="任务A"), workspace_id="default")
    t2 = await svc.create(TaskCreate(title="任务B"), workspace_id="default")
    await svc.move(t2.id, "in_progress", actor="alice", workspace_id="default")
    board = await svc.board(workspace_id="default")
    assert board["counts"]["backlog"] == 1
    assert board["counts"]["in_progress"] == 1
    assert board["columns"]["in_progress"][0]["title"] == "任务B"
    assert board["columns"]["in_progress"][0]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_task_sprint_scheduling(sqlite_storage):
    svc = TaskService(sqlite_storage)
    sp_svc = SprintService(sqlite_storage)
    sp = await sp_svc.create(SprintCreate(name="Sprint 24-08", start_date=_dt(),
                                          end_date=_dt(14)), workspace_id="default")
    t = await svc.create(TaskCreate(title="排期到迭代", sprint_id=sp.id, due_at=_dt(7)),
                         workspace_id="default")
    assert t.sprint_id == sp.id
    items = await svc.list(sprint_id=sp.id, workspace_id="default")
    assert len(items) == 1 and items[0].id == t.id
    # 迭代列表
    sprints = await sp_svc.list(workspace_id="default")
    assert len(sprints) == 1 and sprints[0].name == "Sprint 24-08"
    # 结束早于开始拒绝
    with pytest.raises(ValueError, match="结束时间"):
        await sp_svc.create(SprintCreate(name="Bad", start_date=_dt(14), end_date=_dt()),
                            workspace_id="default")


@pytest.mark.asyncio
async def test_task_upload_plan_registers_attachment_and_link(sqlite_storage):
    svc = TaskService(sqlite_storage)
    t = await svc.create(TaskCreate(title="带方案的任务"), workspace_id="default")
    t2 = await svc.upload_plan(t.id, "https://docs.xxx/plan.pdf", actor="alice",
                               workspace_id="default")
    assert "https://docs.xxx/plan.pdf" in t2.plan_links
    ats = await sqlite_storage.attachment_list("task", t.id, workspace_id="default")
    assert len(ats) == 1 and ats[0]["url"] == "https://docs.xxx/plan.pdf"
    # 去重：同一 URL 不重复登记
    await svc.upload_plan(t.id, "https://docs.xxx/plan.pdf", actor="alice", workspace_id="default")
    t3 = await svc.get(t.id, workspace_id="default")
    assert len(t3.plan_links) == 1


@pytest.mark.asyncio
async def test_task_link_requirement(sqlite_storage):
    """已审核需求 → 开发任务；未审核拒绝。"""
    svc = TaskService(sqlite_storage)
    # 准备已审核需求
    from decp_core.services import FeedbackService, RequirementService
    fb_svc = FeedbackService(sqlite_storage)
    from decp_core.models import FeedbackCreate
    fb = await fb_svc.create(FeedbackCreate(content="客户需要导出功能", module="报表"),
                             workspace_id="default")
    req_svc = RequirementService(sqlite_storage, fb_svc)
    draft = await req_svc.generate_draft(feedback_ids=[fb.id], workspace_id="default")
    req = await req_svc.create(draft, workspace_id="default")
    await req_svc.review(req.id, "accept", reviewer="pm", workspace_id="default")
    # 转开发任务
    t = await svc.link_requirement(req.id, actor="pm", workspace_id="default")
    assert t.type == "requirement"
    assert t.requirement_id == req.id
    assert t.status == "backlog"
    # 继承反馈来源（RequirementOrm.feedback_ids 为字符串 id）
    assert fb.id in t.feedback_ids
    # 未审核需求拒绝
    draft2 = await req_svc.generate_draft(feedback_ids=[fb.id], workspace_id="default")
    req2 = await req_svc.create(draft2, workspace_id="default")
    with pytest.raises(ValueError, match="仅已审核"):
        await svc.link_requirement(req2.id, actor="pm", workspace_id="default")


@pytest.mark.asyncio
async def test_task_link_bug_bidirectional(sqlite_storage):
    svc = TaskService(sqlite_storage)
    bug_svc = BugService(sqlite_storage)
    t = await svc.create(TaskCreate(title="修复任务"), workspace_id="default")
    b = await bug_svc.create(BugCreate(title="登录超时", severity="high"), workspace_id="default")
    t2 = await svc.link_bug(t.id, b.id, workspace_id="default")
    assert b.id in t2.bug_ids
    b2 = await bug_svc.get(b.id, workspace_id="default")
    assert t.id in b2.task_ids


@pytest.mark.asyncio
async def test_task_board_embeds_bug_subcards(sqlite_storage):
    """task.board include_bugs 经 bug_get_many 批量内嵌关联缺陷子卡片（跨后端可用）。"""
    svc = TaskService(sqlite_storage)
    bug_svc = BugService(sqlite_storage)
    t = await svc.create(TaskCreate(title="修复任务"), workspace_id="default")
    b = await bug_svc.create(BugCreate(title="登录超时", severity="high"), workspace_id="default")
    await svc.link_bug(t.id, b.id, workspace_id="default")
    board = await svc.board(workspace_id="default")
    col = next(v for v in board["columns"].values() if any(c["id"] == t.id for c in v))
    card = next(c for c in col if c["id"] == t.id)
    assert card["bugs"] == [{"id": b.id, "title": "登录超时", "status": "new", "severity": "high"}]


@pytest.mark.asyncio
async def test_task_archive_restore(sqlite_storage):
    svc = TaskService(sqlite_storage)
    t = await svc.create(TaskCreate(title="待归档"), workspace_id="default")
    t2 = await svc.archive(t.id, archived_by="alice", workspace_id="default")
    assert t2.archived is True
    # 默认查询不含已归档
    assert await svc.list(workspace_id="default") == []
    # 含归档查询
    items = await svc.list(include_archived=True, workspace_id="default")
    assert len(items) == 1
    # 恢复
    t3 = await svc.restore(t.id, workspace_id="default")
    assert t3.archived is False
    assert len(await svc.list(workspace_id="default")) == 1


@pytest.mark.asyncio
async def test_task_log_activity(sqlite_storage):
    svc = TaskService(sqlite_storage)
    t = await svc.create(TaskCreate(title="审计任务"), workspace_id="default")
    await svc.move(t.id, "in_progress", actor="alice", workspace_id="default")
    await svc.upload_plan(t.id, "https://d/1.pdf", actor="bob", workspace_id="default")
    logs = await svc.log(t.id, workspace_id="default")
    actions = [l.action for l in logs]
    assert "created" in actions
    assert "move" in actions
    assert "plan_added" in actions
    # actor 审计
    assert logs[1].actor == "alice"


@pytest.mark.asyncio
async def test_task_update_datetime_string(sqlite_storage):
    """task_update 直接传 ISO 字符串 due_at 不崩溃（LLM 直传场景）。"""
    svc = TaskService(sqlite_storage)
    t = await svc.create(TaskCreate(title="排期"), workspace_id="default")
    t2 = await svc.update(t.id, {"due_at": "2026-08-20T10:00:00"}, actor="alice",
                          workspace_id="default")
    assert t2.due_at is not None
    # datetime 值写 due_changed 日志不崩溃（JSON 序列化安全）
    from datetime import datetime, timezone
    await svc.update(t.id, {"due_at": datetime.now(timezone.utc)}, actor="alice",
                     workspace_id="default")
    logs = await svc.log(t.id, workspace_id="default")
    assert any(l.action == "due_changed" and l.new_value for l in logs)


@pytest.mark.asyncio
async def test_task_move_reopen_clears_done_at(sqlite_storage):
    """done → in_progress reopen 时清空 done_at，避免残留旧完成时间。"""
    svc = TaskService(sqlite_storage)
    t = await svc.create(TaskCreate(title="reopen"), workspace_id="default")
    await svc.move(t.id, "done", actor="alice", workspace_id="default")
    t2 = await svc.move(t.id, "in_progress", actor="alice", workspace_id="default")
    assert t2.status == "in_progress"
    assert t2.done_at is None


@pytest.mark.asyncio
async def test_task_extra_datetime_json_safe(sqlite_storage):
    """extra 内含 datetime 时 JSON 序列化安全。"""
    from datetime import datetime, timezone
    svc = TaskService(sqlite_storage)
    t = await svc.create(TaskCreate(title="extra", extra={"d": datetime.now(timezone.utc)}),
                         workspace_id="default")
    assert "d" in t.extra
