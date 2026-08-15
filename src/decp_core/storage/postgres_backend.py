"""PostgreSQL 存储后端：psycopg3 连接池实现（生产形态）。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from decp_core.storage.base import StorageBackend

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    channel     TEXT NOT NULL DEFAULT 'natural_language',
    customer    TEXT,
    module      TEXT,
    feedback_type TEXT,
    impact      TEXT,
    source_ref  TEXT,
    submitted_by TEXT DEFAULT 'maintainer',
    structured  JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_customer ON feedback(customer);
CREATE INDEX IF NOT EXISTS idx_feedback_module  ON feedback(module);

CREATE TABLE IF NOT EXISTS requirement (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    module      TEXT,
    priority    TEXT NOT NULL DEFAULT 'P2',
    status      TEXT NOT NULL DEFAULT 'draft',
    feedback_ids JSONB NOT NULL DEFAULT '[]',
    source_refs JSONB NOT NULL DEFAULT '[]',
    cluster_id  TEXT,
    impact_customers INTEGER DEFAULT 0,
    similar_feedback_count INTEGER DEFAULT 0,
    confidence  DOUBLE PRECISION DEFAULT 0.0,
    tags        JSONB NOT NULL DEFAULT '[]',
    extra       JSONB NOT NULL DEFAULT '{}',
    version     INTEGER DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    approved_by TEXT,
    approved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_requirement_status ON requirement(status);
CREATE INDEX IF NOT EXISTS idx_requirement_priority ON requirement(priority);

CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value JSONB
);
"""


class PostgresStorage(StorageBackend):
    """PostgreSQL 实现（JSONB 存储结构化字段，TIMESTAMPTZ 存储时间）。"""

    def __init__(self, dsn: str, *, pool_min: int = 1, pool_max: int = 10) -> None:
        self._dsn = dsn
        self._pool: AsyncConnectionPool | None = None
        self._pool_min = pool_min
        self._pool_max = pool_max

    async def connect(self) -> None:
        self._pool = AsyncConnectionPool(
            self._dsn, min_size=self._pool_min, max_size=self._pool_max,
            open=False, kwargs={"row_factory": dict_row},
        )
        await self._pool.open()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    def _p(self) -> AsyncConnectionPool:
        if self._pool is None:
            raise RuntimeError("Postgres storage not connected")
        return self._pool

    async def init_schema(self) -> None:
        async with self._p().connection() as conn:
            await conn.execute(_SCHEMA)

    @staticmethod
    def _iso(v: datetime | None) -> Any:
        return v if v is None else v.isoformat()

    @staticmethod
    def _to_json(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False, default=str)

    # ---- feedback ----
    async def feedback_insert(self, rec: dict[str, Any]) -> str:
        async with self._p().connection() as conn:
            await conn.execute(
                """INSERT INTO feedback
                   (id, content, channel, customer, module, feedback_type, impact,
                    source_ref, submitted_by, structured, created_at)
                   VALUES (%(id)s,%(content)s,%(channel)s,%(customer)s,%(module)s,
                           %(feedback_type)s,%(impact)s,%(source_ref)s,%(submitted_by)s,
                           %(structured)s,%(created_at)s)""",
                {
                    "id": rec["id"], "content": rec["content"],
                    "channel": rec.get("channel", "natural_language"),
                    "customer": rec.get("customer"), "module": rec.get("module"),
                    "feedback_type": rec.get("feedback_type"), "impact": rec.get("impact"),
                    "source_ref": rec.get("source_ref"),
                    "submitted_by": rec.get("submitted_by", "maintainer"),
                    "structured": self._to_json(rec.get("structured", {})),
                    "created_at": self._iso(rec["created_at"]),
                },
            )
        return rec["id"]

    async def feedback_get(self, fid: str) -> dict[str, Any] | None:
        async with self._p().connection() as conn:
            row = await conn.execute("SELECT * FROM feedback WHERE id=%s", (fid,))
            return await row.fetchone()

    async def feedback_list(self, *, customer=None, module=None, limit=100, offset=0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM feedback"
        conds, args = [], []
        if customer:
            conds.append("customer=%s"); args.append(customer)
        if module:
            conds.append("module=%s"); args.append(module)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        args += [limit, offset]
        async with self._p().connection() as conn:
            cur = await conn.execute(sql, tuple(args))
            rows = await cur.fetchall()
        return [self._normalize(r) for r in rows]

    async def feedback_count(self) -> int:
        async with self._p().connection() as conn:
            row = await (await conn.execute("SELECT COUNT(*) AS n FROM feedback")).fetchone()
        return int(row["n"])

    @staticmethod
    def _normalize(d: dict[str, Any] | None) -> dict[str, Any] | None:
        """Postgres 返回 JSONB 已解析为 dict，datetime 保持 iso 化便于跨后端一致。"""
        if d is None:
            return None
        out = dict(d)
        for k in ("structured", "feedback_ids", "source_refs", "tags", "extra"):
            if isinstance(out.get(k), (str,)):
                try:
                    out[k] = json.loads(out[k])
                except (TypeError, ValueError):
                    pass
        for k in ("created_at", "updated_at", "approved_at"):
            if isinstance(out.get(k), datetime):
                out[k] = out[k].isoformat()
        return out

    # ---- requirement ----
    async def requirement_insert(self, rec: dict[str, Any]) -> str:
        async with self._p().connection() as conn:
            await conn.execute(
                """INSERT INTO requirement
                   (id, title, description, module, priority, status, feedback_ids,
                    source_refs, cluster_id, impact_customers, similar_feedback_count,
                    confidence, tags, extra, version, created_at, updated_at,
                    approved_by, approved_at)
                   VALUES (%(id)s,%(title)s,%(description)s,%(module)s,%(priority)s,
                           %(status)s,%(feedback_ids)s,%(source_refs)s,%(cluster_id)s,
                           %(impact_customers)s,%(similar_feedback_count)s,%(confidence)s,
                           %(tags)s,%(extra)s,%(version)s,%(created_at)s,%(updated_at)s,
                           %(approved_by)s,%(approved_at)s)""",
                {
                    "id": rec["id"], "title": rec["title"],
                    "description": rec.get("description", ""), "module": rec.get("module"),
                    "priority": rec.get("priority", "P2"), "status": rec.get("status", "draft"),
                    "feedback_ids": self._to_json(rec.get("feedback_ids", [])),
                    "source_refs": self._to_json([s.model_dump() if hasattr(s, "model_dump") else s for s in rec.get("source_refs", [])]),
                    "cluster_id": rec.get("cluster_id"),
                    "impact_customers": rec.get("impact_customers", 0),
                    "similar_feedback_count": rec.get("similar_feedback_count", 0),
                    "confidence": rec.get("confidence", 0.0),
                    "tags": self._to_json(rec.get("tags", [])),
                    "extra": self._to_json(rec.get("extra", {})),
                    "version": rec.get("version", 1),
                    "created_at": self._iso(rec["created_at"]),
                    "updated_at": self._iso(rec.get("updated_at", rec["created_at"])),
                    "approved_by": rec.get("approved_by"),
                    "approved_at": self._iso(rec.get("approved_at")),
                },
            )
        return rec["id"]

    async def requirement_get(self, rid: str) -> dict[str, Any] | None:
        async with self._p().connection() as conn:
            row = await (await conn.execute("SELECT * FROM requirement WHERE id=%s", (rid,))).fetchone()
        return self._normalize(row)

    async def requirement_list(self, *, status=None, priority=None, module=None, limit=100, offset=0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM requirement"
        conds, args = [], []
        if status:
            conds.append("status=%s"); args.append(status)
        if priority:
            conds.append("priority=%s"); args.append(priority)
        if module:
            conds.append("module=%s"); args.append(module)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY updated_at DESC LIMIT %s OFFSET %s"
        args += [limit, offset]
        async with self._p().connection() as conn:
            rows = await (await conn.execute(sql, tuple(args))).fetchall()
        return [self._normalize(r) for r in rows]

    async def requirement_update(self, rid: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not fields:
            return await self.requirement_get(rid)
        updates: dict[str, Any] = {"rid": rid}
        sets: list[str] = []
        for i, (k, v) in enumerate(fields.items()):
            key = f"v{i}"
            if k in ("feedback_ids", "tags", "extra"):
                v = self._to_json(v)
            elif k == "source_refs":
                v = self._to_json([s.model_dump() if hasattr(s, "model_dump") else s for s in v])
            elif isinstance(v, datetime):
                v = self._iso(v)
            sets.append(f"{k}=%({key})s")
            updates[key] = v
        async with self._p().connection() as conn:
            await conn.execute(
                f"UPDATE requirement SET {', '.join(sets)} WHERE id=%(rid)s", updates,
            )
        return await self.requirement_get(rid)

    async def requirement_count(self) -> int:
        async with self._p().connection() as conn:
            row = await (await conn.execute("SELECT COUNT(*) AS n FROM requirement")).fetchone()
        return int(row["n"])

    # ---- app_meta ----
    async def meta_get(self, key: str) -> Any | None:
        async with self._p().connection() as conn:
            row = await (await conn.execute("SELECT value FROM app_meta WHERE key=%s", (key,))).fetchone()
        return row["value"] if row else None

    async def meta_set(self, key: str, value: Any) -> None:
        async with self._p().connection() as conn:
            await conn.execute(
                "INSERT INTO app_meta(key, value) VALUES(%s,%s) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, self._to_json(value)),
            )

    # ---- stats ----
    async def domain_stats(self) -> dict[str, Any]:
        fb = await self.feedback_count()
        req = await self.requirement_count()
        return {"feedback": fb, "requirement": req, "backend": "postgres", "dsn": self._dsn.split("@")[-1]}
