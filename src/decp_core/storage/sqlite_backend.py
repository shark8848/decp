"""SQLite 存储后端：零依赖单文件实现。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

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
    structured  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
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
    feedback_ids TEXT NOT NULL DEFAULT '[]',
    source_refs TEXT NOT NULL DEFAULT '[]',
    cluster_id  TEXT,
    impact_customers INTEGER DEFAULT 0,
    similar_feedback_count INTEGER DEFAULT 0,
    confidence  REAL DEFAULT 0.0,
    tags        TEXT NOT NULL DEFAULT '[]',
    extra       TEXT NOT NULL DEFAULT '{}',
    version     INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_requirement_status ON requirement(status);
CREATE INDEX IF NOT EXISTS idx_requirement_priority ON requirement(priority);

CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _loads(s: str | None, default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


class SQLiteStorage(StorageBackend):
    """SQLite 实现（默认开启 WAL，方便并发读）。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _c(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SQLite storage not connected")
        return self._conn

    async def init_schema(self) -> None:
        self._c().executescript(_SCHEMA)
        self._c().commit()

    # ---- feedback ----
    async def feedback_insert(self, rec: dict[str, Any]) -> str:
        cur = self._c().execute(
            """INSERT INTO feedback
               (id, content, channel, customer, module, feedback_type, impact,
                source_ref, submitted_by, structured, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["id"], rec["content"], rec.get("channel", "natural_language"),
                rec.get("customer"), rec.get("module"), rec.get("feedback_type"),
                rec.get("impact"), rec.get("source_ref"),
                rec.get("submitted_by", "maintainer"),
                _dumps(rec.get("structured", {})), rec["created_at"].isoformat(),
            ),
        )
        self._c().commit()
        return rec["id"]

    async def feedback_get(self, fid: str) -> dict[str, Any] | None:
        row = self._c().execute("SELECT * FROM feedback WHERE id=?", (fid,)).fetchone()
        return self._row_to_feedback(row)

    async def feedback_list(self, *, customer=None, module=None, limit=100, offset=0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM feedback"
        conds, args = [], []
        if customer:
            conds.append("customer=?"); args.append(customer)
        if module:
            conds.append("module=?"); args.append(module)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        rows = self._c().execute(sql, args).fetchall()
        return [self._row_to_feedback(r) for r in rows]

    async def feedback_count(self) -> int:
        row = self._c().execute("SELECT COUNT(*) AS n FROM feedback").fetchone()
        return int(row["n"]) if row else 0

    @staticmethod
    def _row_to_feedback(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        d = dict(row)
        d["structured"] = _loads(d.get("structured"), {})
        return d

    # ---- requirement ----
    async def requirement_insert(self, rec: dict[str, Any]) -> str:
        cur = self._c().execute(
            """INSERT INTO requirement
               (id, title, description, module, priority, status, feedback_ids,
                source_refs, cluster_id, impact_customers, similar_feedback_count,
                confidence, tags, extra, version, created_at, updated_at,
                approved_by, approved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["id"], rec["title"], rec.get("description", ""), rec.get("module"),
                rec.get("priority", "P2"), rec.get("status", "draft"),
                _dumps(rec.get("feedback_ids", [])),
                _dumps([s.model_dump() if hasattr(s, "model_dump") else s for s in rec.get("source_refs", [])]),
                rec.get("cluster_id"), rec.get("impact_customers", 0),
                rec.get("similar_feedback_count", 0), rec.get("confidence", 0.0),
                _dumps(rec.get("tags", [])), _dumps(rec.get("extra", {})),
                rec.get("version", 1), rec["created_at"].isoformat(),
                rec.get("updated_at", rec["created_at"]).isoformat(),
                rec.get("approved_by"), rec.get("approved_at").isoformat() if rec.get("approved_at") else None,
            ),
        )
        self._c().commit()
        return rec["id"]

    async def requirement_get(self, rid: str) -> dict[str, Any] | None:
        row = self._c().execute("SELECT * FROM requirement WHERE id=?", (rid,)).fetchone()
        return self._row_to_requirement(row)

    async def requirement_list(self, *, status=None, priority=None, module=None, limit=100, offset=0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM requirement"
        conds, args = [], []
        if status:
            conds.append("status=?"); args.append(status)
        if priority:
            conds.append("priority=?"); args.append(priority)
        if module:
            conds.append("module=?"); args.append(module)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        rows = self._c().execute(sql, args).fetchall()
        return [self._row_to_requirement(r) for r in rows]

    async def requirement_update(self, rid: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not fields:
            return await self.requirement_get(rid)
        sets, args = [], []
        for k, v in fields.items():
            if k == "feedback_ids":
                v = _dumps(v)
            elif k == "source_refs":
                v = _dumps([s.model_dump() if hasattr(s, "model_dump") else s for s in v])
            elif k == "tags":
                v = _dumps(v)
            elif k == "extra":
                v = _dumps(v)
            elif isinstance(v, datetime):
                v = v.isoformat()
            sets.append(f"{k}=?"); args.append(v)
        args.append(rid)
        cur = self._c().execute(f"UPDATE requirement SET {', '.join(sets)} WHERE id=?", args)
        self._c().commit()
        if cur.rowcount == 0:
            return None
        return await self.requirement_get(rid)

    async def requirement_count(self) -> int:
        row = self._c().execute("SELECT COUNT(*) AS n FROM requirement").fetchone()
        return int(row["n"]) if row else 0

    @staticmethod
    def _row_to_requirement(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        d = dict(row)
        d["feedback_ids"] = _loads(d.get("feedback_ids"), [])
        d["source_refs"] = _loads(d.get("source_refs"), [])
        d["tags"] = _loads(d.get("tags"), [])
        d["extra"] = _loads(d.get("extra"), {})
        return d

    # ---- app_meta ----
    async def meta_get(self, key: str) -> Any | None:
        row = self._c().execute("SELECT value FROM app_meta WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        return _loads(row["value"], None)

    async def meta_set(self, key: str, value: Any) -> None:
        self._c().execute(
            "INSERT INTO app_meta(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, _dumps(value)),
        )
        self._c().commit()

    # ---- stats ----
    async def domain_stats(self) -> dict[str, Any]:
        fb = await self.feedback_count()
        req = await self.requirement_count()
        return {"feedback": fb, "requirement": req, "backend": "sqlite", "path": str(self._path)}
