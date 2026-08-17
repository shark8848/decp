"""Service 层测试：去重、聚类、分类、影响分析、优先级、来源校验。"""
from __future__ import annotations

import pytest

from decp_core.logging_setup import get_decp_logger
from decp_core.models import FeedbackCreate
from decp_core.services import FeedbackService, RequirementService, similarity


def test_similarity_basic():
    a = "客户 A 导入超过 5000 条订单时失败，影响月度结算。"
    b = "客户 A 导入超过 5000 条订单时失败，影响月度结算。"
    assert similarity(a, b) > 0.9
    c = "今天天气很好，适合出去玩。"
    assert similarity(a, c) < 0.3


@pytest.mark.asyncio
async def test_analyze_pipeline(sqlite_storage, seeded):
    svc = RequirementService(sqlite_storage)
    analysis = await svc.analyze()
    # 分类覆盖
    assert len(analysis.categories) >= 3
    # 去重：同义重复的两条订单反馈应分到同一组
    ids0, ids1 = seeded[0].id, seeded[1].id
    joined = [g for g in analysis.duplicate_groups if ids0 in g and ids1 in g]
    assert len(joined) == 1
    # 聚类：同类反馈聚合（订单类两条应同簇）
    cluster_of0 = next(c for c in analysis.clusters if ids0 in c["feedback_ids"])
    assert ids1 in cluster_of0["feedback_ids"]
    assert len(analysis.clusters) >= 3
    assert all(c["count"] >= 1 for c in analysis.clusters)
    # 优先级：全部存在 P0-P3 之一
    assert all(p in {"P0", "P1", "P2", "P3"} for p in analysis.priorities.values())
    # 影响分析
    assert len(analysis.impact) == len(seeded)
    # 来源校验
    assert len(analysis.sources_verified) == len(seeded)


@pytest.mark.asyncio
async def test_generate_draft_and_review(sqlite_storage, seeded):
    svc = RequirementService(sqlite_storage)
    req = await svc.generate_draft()
    assert req.status == "draft"
    assert req.confidence > 0
    assert len(req.feedback_ids) >= 1
    assert len(req.source_refs) >= 1
    assert req.source_refs[0].ref_type == "feedback"

    # 审核接受 → version+1, approved
    reviewed = await svc.review(req.id, "accept", "pm")
    assert reviewed.status == "accepted"
    assert reviewed.version == 2
    assert reviewed.approved_by == "pm"
    assert reviewed.approved_at is not None

    # 拒绝
    req2 = await svc.generate_draft()
    rejected = await svc.review(req2.id, "reject", "pm")
    assert rejected.status == "rejected"


@pytest.mark.asyncio
async def test_archive_restore(sqlite_storage, seeded):
    svc = RequirementService(sqlite_storage)
    req = await svc.generate_draft()
    assert req.archived is False

    # 未审核（draft）不可归档
    with pytest.raises(ValueError, match="未完成审核"):
        await svc.archive(req.id)

    # 审核后归档
    reviewed = await svc.review(req.id, "accept", "pm")
    archived = await svc.archive(reviewed.id, "pm-zhang")
    assert archived.archived is True
    assert archived.archived_by == "pm-zhang"
    assert archived.archived_at is not None
    assert archived.version == reviewed.version + 1

    # 重复归档幂等
    again = await svc.archive(reviewed.id, "pm-zhang")
    assert again.archived is True

    # 默认 list 不含归档；include_archived 含
    active = await svc.list()
    assert all(not r.archived for r in active)
    incl = await svc.list(include_archived=True)
    assert any(r.id == reviewed.id for r in incl)

    # 恢复
    restored = await svc.restore(reviewed.id)
    assert restored.archived is False
    assert restored.archived_at is None
    assert restored.archived_by is None
    assert restored.status == "accepted"  # 保留状态历史

    # 恢复未归档需求幂等
    again_restore = await svc.restore(reviewed.id)
    assert again_restore.archived is False


@pytest.mark.asyncio
async def test_archive_requires_review(sqlite_storage, seeded):
    """draft / reviewing 不可归档，须先完成人工审核。"""
    svc = RequirementService(sqlite_storage)
    draft = await svc.generate_draft()
    assert draft.status == "draft"
    with pytest.raises(ValueError):
        await svc.archive(draft.id)


@pytest.mark.asyncio
async def test_archive_business_log_points(sqlite_storage, seeded, caplog):
    """归档/恢复业务日志打点：requirement.archived / requirement.restored。"""
    import logging

    svc = RequirementService(sqlite_storage)
    req = await svc.generate_draft()
    await svc.review(req.id, "accept", "pm")

    svc_logger = get_decp_logger("service")
    with caplog.at_level(logging.INFO, logger=svc_logger.name):
        await svc.archive(req.id, "pm-zhang")
        await svc.restore(req.id)

    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "requirement.archived" in msgs and req.id in msgs
    assert "requirement.restored" in msgs and req.id in msgs


@pytest.mark.asyncio
async def test_find_duplicates_threshold(sqlite_storage, seeded):
    svc = RequirementService(sqlite_storage)
    dup = svc.find_duplicates(seeded)
    # 至少有一组重复（同义重复对）
    assert len(dup) >= 1
    # 每条反馈至多出现在一个分组中
    all_ids = [fid for g in dup for fid in g]
    assert len(all_ids) == len(set(all_ids))


@pytest.mark.asyncio
async def test_business_log_points(sqlite_storage, caplog):
    """业务日志打点：service 层关键方法必须产生结构化日志（供日志中心链路检索）。"""
    import logging

    svc_logger = get_decp_logger("service")
    with caplog.at_level(logging.INFO, logger=svc_logger.name):
        # feedback.created
        fbs = FeedbackService(sqlite_storage)
        fb = await fbs.create(FeedbackCreate(
            content="登录页加载超过10秒", customer="Acme", module="portal",
        ))
        # requirement.created / draft_generated
        req_svc = RequirementService(sqlite_storage, fbs)
        req = await req_svc.generate_draft()
        # requirement.reviewed
        await req_svc.review(req.id, "accept", "pm-zhang")

    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "feedback.created" in msgs and fb.id in msgs
    assert "requirement.created" in msgs and req.id in msgs
    assert "requirement.draft_generated" in msgs and req.id in msgs
    assert "requirement.reviewed" in msgs and "pm-zhang" in msgs
    # analyze 走 generate_draft 内部也会打点
    assert "requirement.analyzed" in msgs
