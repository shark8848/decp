"""存储层测试：SQLite 全量 + PostgreSQL（可用时自动跳过）。"""
from __future__ import annotations

import pytest

from decp_core.models import Feedback, RequirementCreate, SourceRef, new_id, utcnow
from decp_core.storage import create_storage


@pytest.mark.asyncio
async def test_sqlite_feedback_crud(sqlite_storage):
    now = utcnow()
    rec = Feedback(
        id=new_id("fb"), content="客户 A 导入订单失败",
        customer="Customer A", module="订单导入", channel="ticket",
        source_ref="T-001", submitted_by="maintainer",
        created_at=now, structured={"feedback_type": "性能"},
    )
    await sqlite_storage.feedback_insert(rec.model_dump())
    got = await sqlite_storage.feedback_get(rec.id)
    assert got is not None
    assert got["content"] == "客户 A 导入订单失败"
    assert got["structured"]["feedback_type"] == "性能"

    items = await sqlite_storage.feedback_list(customer="Customer A")
    assert len(items) == 1
    assert await sqlite_storage.feedback_count() == 1


@pytest.mark.asyncio
async def test_sqlite_requirement_crud(sqlite_storage):
    now = utcnow()
    rec = RequirementCreate(
        title="提升批量订单导入能力", module="订单导入", priority="P1",
        feedback_ids=["fb-1", "fb-2"],
        source_refs=[SourceRef(ref_type="feedback", ref_id="fb-1")],
        impact_customers=5, similar_feedback_count=12, confidence=0.89,
        tags=["性能"], extra={"cluster_count": 2},
    )
    rid = await sqlite_storage.requirement_insert(
        {**rec.model_dump(), "id": new_id("req"), "created_at": now, "updated_at": now, "version": 1}
    )
    got = await sqlite_storage.requirement_get(rid)
    assert got["title"] == "提升批量订单导入能力"
    assert got["feedback_ids"] == ["fb-1", "fb-2"]
    assert got["source_refs"][0]["ref_id"] == "fb-1"
    assert got["tags"] == ["性能"]

    # update
    updated = await sqlite_storage.requirement_update(rid, {"status": "accepted", "priority": "P0"})
    assert updated["status"] == "accepted"
    assert updated["priority"] == "P0"

    items = await sqlite_storage.requirement_list(status="accepted")
    assert len(items) == 1


@pytest.mark.asyncio
async def test_requirement_archived_filter(sqlite_storage):
    """归档过滤：默认隐藏 archived，include_archived 显式包含。"""
    now = utcnow()
    rec = RequirementCreate(title="归档过滤测试", module="归档", priority="P2")
    rid = await sqlite_storage.requirement_insert(
        {**rec.model_dump(), "id": new_id("req"), "created_at": now, "updated_at": now, "version": 1}
    )
    # 默认列表含活跃
    assert len(await sqlite_storage.requirement_list()) == 1
    # 归档
    await sqlite_storage.requirement_update(rid, {"archived": True})
    assert len(await sqlite_storage.requirement_list()) == 0
    assert len(await sqlite_storage.requirement_list(include_archived=True)) == 1
    # count 同样区分
    assert await sqlite_storage.requirement_count() == 0
    assert await sqlite_storage.requirement_count(include_archived=True) == 1
    # 归档记录字段回读
    got = await sqlite_storage.requirement_get(rid)
    assert got["archived"] is True


@pytest.mark.asyncio
async def test_meta(sqlite_storage):
    await sqlite_storage.meta_set("schema_version", 1)
    assert await sqlite_storage.meta_get("schema_version") == 1
    await sqlite_storage.meta_set("schema_version", 2)
    assert await sqlite_storage.meta_get("schema_version") == 2


@pytest.mark.asyncio
async def test_domain_stats(sqlite_storage):
    stats = await sqlite_storage.domain_stats()
    assert stats["backend"] == "sqlite"
    assert "feedback" in stats and "requirement" in stats


# ---------------------------------------------------------------------------
# PostgreSQL（存在可用环境时运行；无则跳过）
# ---------------------------------------------------------------------------

def _pg_settings() -> dict:
    """从全局配置（.env / 环境变量）读取 PG 连接信息。"""
    from decp_core.config import settings

    return {
        "storage_backend": "postgres",
        "pg_host": settings.pg_host,
        "pg_port": settings.pg_port,
        "pg_db": settings.pg_db,  # 复用业务库（decp 用户无建库权限）
        "pg_user": settings.pg_user,
        "pg_password": settings.pg_password,
    }


@pytest.mark.asyncio
async def test_postgres_crud(tmp_path):
    """PostgreSQL 后端 CRUD。需 .env 或环境变量提供连接信息，否则 skip。"""
    from decp_core.config import Settings

    cfg = _pg_settings()
    if not cfg["pg_password"]:
        pytest.skip("未配置 DECP_PG_PASSWORD，跳过 PostgreSQL 测试")

    s = Settings(**cfg)
    storage = create_storage(s)
    try:
        await storage.connect()
        await storage.init_schema()
        now = utcnow()
        rec = Feedback(
            id=new_id("fb"), content="PG 测试反馈", customer="Customer X",
            module="test", channel="api", created_at=now,
            structured={"feedback_type": "功能"},
        )
        await storage.feedback_insert(rec.model_dump())
        got = await storage.feedback_get(rec.id)
        assert got is not None
        assert got["content"] == "PG 测试反馈"
        assert got["structured"]["feedback_type"] == "功能"

        rnow = utcnow()
        req = RequirementCreate(title="PG 需求", priority="P2", feedback_ids=["fb-pg1"],
                                source_refs=[SourceRef(ref_type="api", ref_id="fb-pg1")])
        rid = await storage.requirement_insert(
            {**req.model_dump(), "id": new_id("req"), "created_at": rnow, "updated_at": rnow, "version": 1}
        )
        got_r = await storage.requirement_get(rid)
        assert got_r["feedback_ids"] == ["fb-pg1"]
        upd = await storage.requirement_update(rid, {"status": "accepted"})
        assert upd["status"] == "accepted"

        stats = await storage.domain_stats()
        assert stats["backend"] == "postgres"
    finally:
        await storage.close()


def test_build_dsn_urlencodes_password():
    """密码/用户名含 URL 保留字符时必须百分号编码，否则污染 host 解析。"""
    from sqlalchemy.engine import make_url

    from decp_core.config import Settings
    from decp_core.storage import build_dsn

    s = Settings(
        storage_backend="postgres",
        pg_host="10.0.0.1", pg_port=5432,
        pg_db="decp", pg_user="decp", pg_password="p@ss#wo$rd!",
    )
    u = make_url(build_dsn(s))
    assert u.host == "10.0.0.1"
    assert u.port == 5432
    assert u.username == "decp"
    assert u.password == "p@ss#wo$rd!"
    # 编码后的 DSN 不应含未编码的保留字符
    dsn = build_dsn(s)
    assert "p@ss#wo$rd!" not in dsn
    assert "@10.0.0.1" in dsn  # host 前的 @ 是分隔符，不受影响


def test_build_dsn_plain_password_unchanged():
    """普通密码（无保留字符）编码后语义不变。"""
    from sqlalchemy.engine import make_url

    from decp_core.config import Settings
    from decp_core.storage import build_dsn

    s = Settings(
        storage_backend="postgres",
        pg_host="db.internal", pg_port=6000,
        pg_db="decp", pg_user="pm", pg_password="simple-pass",
    )
    u = make_url(build_dsn(s))
    assert u.host == "db.internal"
    assert u.username == "pm"
    assert u.password == "simple-pass"
