"""会议纪要测试：启发式提取、存档、待办转任务、缺陷识别、来源追溯。"""
from __future__ import annotations

import pytest

from decp_core.models import MeetingMinutesCreate
from decp_core.services import MeetingMinutesService

SAMPLE_RAW = """
时间：2026-08-18
参会人：张三、李四
会议内容：讨论报表模块重构进展
决议：下周三上线重构版本
待办：
1. 完成报表导出接口（张三，周五前）
2. 跟进部署安排（李四）
3. 技术债：重构登录模块鉴权
"""


@pytest.mark.asyncio
async def test_meeting_submit_extracts_structured(sqlite_storage):
    svc = MeetingMinutesService(sqlite_storage)
    m = await svc.submit(MeetingMinutesCreate(title="周会", raw_text=SAMPLE_RAW),
                         workspace_id="default")
    assert m.id.startswith("mt-")
    assert "讨论报表模块" in m.summary
    assert len(m.decisions) == 1
    assert m.decisions[0]["item"] == "下周三上线重构版本"
    # 待办提取：责任人 + 分类
    assert len(m.action_items) == 3
    ai = m.action_items[0]
    assert ai.desc.startswith("完成报表导出接口")
    assert ai.owner == "张三"
    assert ai.kind == "dev"
    # 跟进 → chore
    assert m.action_items[1].kind == "chore"
    # 原文保留
    assert "时间：2026-08-18" in m.raw_text


@pytest.mark.asyncio
async def test_meeting_classify_type(sqlite_storage):
    svc = MeetingMinutesService
    assert svc._classify_type("实现登录接口") == "project"
    assert svc._classify_type("技术债重构") == "tech_debt"
    assert svc._classify_type("运营活动配置") == "ops"
    assert svc._classify_type("跟进客户沟通") == "chore"


@pytest.mark.asyncio
async def test_meeting_to_tasks_dry_run_and_commit(sqlite_storage):
    svc = MeetingMinutesService(sqlite_storage)
    m = await svc.submit(MeetingMinutesCreate(title="周会", raw_text=SAMPLE_RAW),
                         workspace_id="default")
    # dry_run 预览
    dry = await svc.to_tasks(m.id, actor="alice", workspace_id="default", dry_run=True)
    assert len(dry) == 3
    types = {d["type"] for d in dry}
    assert "tech_debt" in types  # 技术债重构
    # 确认后入库
    real = await svc.to_tasks(m.id, actor="alice", workspace_id="default", dry_run=False)
    assert len(real) == 3
    for r in real:
        assert r["task_id"].startswith("ts-")
        assert r["status"] == "backlog"
    # 来源追溯：任务含 meeting source_ref
    from decp_core.services import TaskService
    task_svc = TaskService(sqlite_storage)
    tasks = await task_svc.list(workspace_id="default")
    assert len(tasks) == 3
    refs = [s for t in tasks for s in t.source_refs]
    assert any(s.ref_type == "meeting" and s.ref_id == m.id for s in refs)


@pytest.mark.asyncio
async def test_meeting_to_bugs(sqlite_storage):
    svc = MeetingMinutesService(sqlite_storage)
    m = await svc.submit(MeetingMinutesCreate(
        title="bug 会", raw_text="发现登录接口报错，需要排查；另需确认部署安排"),
        workspace_id="default")
    bugs = await svc.to_bugs(m.id, actor="alice", workspace_id="default", dry_run=True)
    assert len(bugs) >= 1
    real = await svc.to_bugs(m.id, actor="alice", workspace_id="default", dry_run=False)
    assert len(real) >= 1
    from decp_core.services import BugService
    bug_svc = BugService(sqlite_storage)
    got = await bug_svc.get(real[0]["bug_id"], workspace_id="default")
    assert got.channel == "meeting"
    assert m.id in got.meeting_ids


@pytest.mark.asyncio
async def test_meeting_search_cjk_participant(sqlite_storage):
    """中文参与人检索（SQLite JSON 存 unicode 转义，LIKE 失效，须 Python 层过滤）。"""
    svc = MeetingMinutesService(sqlite_storage)
    await svc.submit(MeetingMinutesCreate(title="周会", participants=["张三"], raw_text="x"),
                     workspace_id="default")
    items = await svc.list(participant="张三", workspace_id="default")
    assert len(items) == 1
    items2 = await svc.list(participant="李四", workspace_id="default")
    assert len(items2) == 0


@pytest.mark.asyncio
async def test_meeting_list_and_get(sqlite_storage):
    svc = MeetingMinutesService(sqlite_storage)
    m1 = await svc.submit(MeetingMinutesCreate(title="周会", raw_text=SAMPLE_RAW,
                                               module="报表"), workspace_id="default")
    m2 = await svc.submit(MeetingMinutesCreate(title="评审会", raw_text="评审 API 设计",
                                               module="API"), workspace_id="default")
    items = await svc.list(module="报表", workspace_id="default")
    assert len(items) == 1 and items[0].id == m1.id
    got = await svc.get(m1.id, workspace_id="default")
    assert got.title == "周会"
    # 隔离
    assert await svc.get(m1.id, workspace_id="other") is None
    assert len(await svc.list(workspace_id="other")) == 0


@pytest.mark.asyncio
async def test_meeting_to_tasks_lenient_owner(sqlite_storage):
    """纪要责任人为非成员时不指派（宽容），不阻塞整批生成。"""
    svc = MeetingMinutesService(sqlite_storage)
    m = await svc.submit(MeetingMinutesCreate(
        title="会", raw_text="待办：\n1. 实现报表接口（王五，周五前）\n2. 跟进排期"),
        workspace_id="default")
    real = await svc.to_tasks(m.id, actor="alice", workspace_id="default", dry_run=False)
    assert len(real) == 2
    # 王五非成员，任务 assignee 应为 None（宽容降级），extra 记录备注
    from decp_core.services import TaskService
    tasks = await TaskService(sqlite_storage).list(workspace_id="default")
    assert all(t.assignee is None for t in tasks)
    assert any("王五" in (t.extra.get("meeting_note") or "") for t in tasks)


@pytest.mark.asyncio
async def test_meeting_to_tasks_idempotent(sqlite_storage):
    """重复确认/重试不产生重复任务（幂等）。"""
    svc = MeetingMinutesService(sqlite_storage)
    m = await svc.submit(MeetingMinutesCreate(
        title="会", raw_text="待办：\n1. 实现报表接口\n2. 跟进排期"),
        workspace_id="default")
    r1 = await svc.to_tasks(m.id, actor="alice", workspace_id="default", dry_run=False)
    r2 = await svc.to_tasks(m.id, actor="alice", workspace_id="default", dry_run=False)
    assert len(r1) == 2
    assert len(r2) == 2
    # 同一会议生成的 task_id 集合一致（顺序可能因 created_at 微差不同，按集合比较）
    assert {t["task_id"] for t in r1} == {t["task_id"] for t in r2}
    # 该会议应只生成 2 个任务（不因重跑翻倍）
    existing = await svc._tasks_from_meeting(m.id, workspace_id="default")
    assert len(existing) == 2


@pytest.mark.asyncio
async def test_meeting_to_bugs_idempotent(sqlite_storage):
    svc = MeetingMinutesService(sqlite_storage)
    m = await svc.submit(MeetingMinutesCreate(
        title="bug 会", raw_text="发现登录接口报错"), workspace_id="default")
    r1 = await svc.to_bugs(m.id, actor="alice", workspace_id="default", dry_run=False)
    r2 = await svc.to_bugs(m.id, actor="alice", workspace_id="default", dry_run=False)
    assert len(r1) == 1 and r1[0]["bug_id"] == r2[0]["bug_id"]
    # 该会议应只生成 1 个缺陷（不因重跑翻倍）
    existing = await svc._bugs_from_meeting(m.id, workspace_id="default")
    assert len(existing) == 1


def test_parse_owner_edge_cases():
    """责任人提取边界：时间括号不误判、复合前缀不误判、姓名+时间混合提取。"""
    from decp_core.services import _parse_owner
    assert _parse_owner("张三：完成接口（周五前）") == "张三"   # 冒号前缀人名
    assert _parse_owner("完成接口（周五前）") is None           # 纯时间括号
    assert _parse_owner("完成接口（张三，周五前）") == "张三"   # 姓名+时间
    assert _parse_owner("跟进部署安排：今天完成") is None       # 复合动作前缀
    assert _parse_owner("负责人:李四 完成接口") == "李四"       # 显式标签


def test_parse_due_weekday():
    """截止提取：下周三 / 周五前 均解析到正确星期几。"""
    from datetime import date, timedelta
    from decp_core.services import _parse_due
    d1 = _parse_due("（下周三）")
    assert d1 is not None and d1.weekday() == 2
    d2 = _parse_due("（周五前）")
    assert d2 is not None and d2.weekday() == 4
    # 返回日期晚于今天
    assert d1 > date.today()
