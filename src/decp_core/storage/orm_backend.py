# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""统一 ORM 存储后端：SQLAlchemy 2.0 async 实现，SQLite / PostgreSQL 共用。

通过 engine URL 切换方言（`sqlite+aiosqlite://` 或 `postgresql+psycopg://`），
单一实现消除旧双后端（sqlite_backend / postgres_backend）的重复 CRUD 与 JSON 序列化。

`StorageBackend` 抽象接口契约不变——Service / MCP / Agent / 测试层零改动。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from decp_core.models import utcnow
from decp_core.storage.base import StorageBackend
from decp_core.storage.orm import (
    AppMetaOrm,
    Base,
    FeedbackOrm,
    RequirementOrm,
    UserOrm,
    WorkspaceMemberOrm,
    WorkspaceOrm,
)


def _serialize_refs(refs: Any) -> list[Any]:
    """source_refs 可能是 pydantic SourceRef 对象，统一转 dict 便于 JSON 编码。"""
    if not refs:
        return []
    return [r.model_dump() if hasattr(r, "model_dump") else r for r in refs]


def _to_orm(rec: dict[str, Any]) -> dict[str, Any]:
    """入参 dict → ORM 可写字段（source_refs 里的 pydantic 对象转纯 Python 结构）。

    仅 requirement 记录携带 source_refs；feedback 无此字段，不能凭空注入，
    否则 ORM 构造会报 invalid keyword argument。
    """
    out = dict(rec)
    if "source_refs" in out:
        out["source_refs"] = _serialize_refs(out.get("source_refs"))
    return out


class ORMStorage(StorageBackend):
    """SQLAlchemy 2.0 async ORM 存储后端。"""

    def __init__(self, url: str, *, pool_min: int = 1, pool_max: int = 10,
                 sqlite_path: str | None = None):
        self._url = url
        self._sqlite_path = sqlite_path
        self._engine: AsyncEngine | None = None
        self._pool_min = pool_min
        self._pool_max = pool_max
        # 兼容属性：旧 SQLite 后端暴露 `_path`（Path 对象，测试 decp_tools 用 `_path.parent`）
        self._path: Any = Path(sqlite_path) if sqlite_path else None

    # ---- 生命周期 ----
    async def connect(self) -> None:
        kwargs: dict[str, Any] = {"pool_size": self._pool_min, "max_overflow": self._pool_max - self._pool_min}
        if self._url.startswith("sqlite"):
            kwargs = {"pool_pre_ping": True}  # SQLite 单连接池，不需要 pool_size 语义
        self._engine = create_async_engine(self._url, **kwargs)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    def _e(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("存储未连接：先调用 connect()")
        return self._engine

    def _session(self) -> AsyncSession:
        """创建会话。

        expire_on_commit=False：commit 后属性不标记过期，避免 async 环境
        下退出会话后再访问属性触发同步 lazy refresh（DetachedInstanceError /
        MissingGreenlet）。ORM 场景的标准推荐配置。
        """
        return AsyncSession(self._e(), expire_on_commit=False)

    async def init_schema(self) -> None:
        async with self._e().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # 轻量迁移：旧库补列（create_all 只建表不补列）
        await self._ensure_archive_columns()
        await self._ensure_workspace_columns()

    async def _ensure_archive_columns(self) -> None:
        """幂等补齐 requirement 表归档列（SQLite / PostgreSQL 通用）。"""
        await self._ensure_columns(
            "requirement",
            [
                ("archived", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("archived_at", "TIMESTAMPTZ"),
                ("archived_by", "VARCHAR"),
            ],
        )

    async def _ensure_workspace_columns(self) -> None:
        """幂等补齐多租户相关列（旧库迁移）。

        - feedback / requirement 表 workspace_id 列（存量单租户 → 多租户）
        - workspace 表 passcode 列（通行证加入机制）
        """
        await self._ensure_columns(
            "feedback",
            [("workspace_id", "VARCHAR NOT NULL DEFAULT 'default'")],
        )
        await self._ensure_columns(
            "requirement",
            [("workspace_id", "VARCHAR NOT NULL DEFAULT 'default'")],
        )
        await self._ensure_columns(
            "workspace",
            [("passcode", "VARCHAR")],
        )

    async def _ensure_columns(self, table: str, specs: list[tuple[str, str]]) -> None:
        """轻量迁移：create_all 只建表不补列，旧库仅缺失列时 ALTER TABLE 补齐。

        SQLite / PostgreSQL 通用；表不存在时静默跳过（由 create_all 兜底）。
        存量数据按列定义给默认值，不阻塞既有业务。
        """
        if self._engine is None:
            return
        try:
            async with self._e().begin() as conn:
                def _inspect(sync_conn):
                    import sqlalchemy

                    return {
                        c["name"] for c in sqlalchemy.inspect(sync_conn).get_columns(table)
                    }

                columns = await conn.run_sync(_inspect)
        except Exception:  # noqa: BLE001 — 表不存在等场景由 create_all 兜底，迁移静默跳过
            return
        add = [(name, ddl) for name, ddl in specs if name not in columns]
        if not add:
            return
        async with self._e().begin() as conn:
            for name, ddl in add:
                await conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN "{name}" {ddl}')

    # ---- 用户 ----
    async def user_get(self, user_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(UserOrm, user_id)
            if row is None:
                return None
            return {"user_id": row.user_id, "name": row.name, "created_at": row.created_at}

    async def user_upsert(self, user_id: str, name: str | None = None) -> dict[str, Any]:
        async with self._session() as session:
            row = await session.get(UserOrm, user_id)
            if row is None:
                row = UserOrm(user_id=user_id, name=name, created_at=utcnow())
                session.add(row)
            elif name is not None:
                row.name = name
            await session.commit()
            return {"user_id": row.user_id, "name": row.name, "created_at": row.created_at}

    # ---- workspace ----
    async def workspace_insert(self, rec: dict[str, Any]) -> str:
        async with self._session() as session:
            session.add(WorkspaceOrm(**rec))
            await session.commit()
        return rec["id"]

    async def workspace_get(self, workspace_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(WorkspaceOrm, workspace_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "name": row.name,
                "owner_user_id": row.owner_user_id,
                "description": row.description,
                "passcode": row.passcode,
                "created_at": row.created_at,
            }

    async def workspace_list_by_user(self, user_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(WorkspaceOrm)
            .join(WorkspaceMemberOrm, WorkspaceMemberOrm.workspace_id == WorkspaceOrm.id)
            .where(
                WorkspaceMemberOrm.user_id == user_id,
                WorkspaceMemberOrm.status == "approved",
            )
            .order_by(WorkspaceOrm.created_at.desc())
        )
        async with self._session() as session:
            rows = (await session.scalars(stmt)).all()
            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "owner_user_id": r.owner_user_id,
                    "description": r.description,
                    "passcode": r.passcode,
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    # ---- workspace_member ----
    async def member_upsert(
        self, workspace_id: str, user_id: str, *, role: str = "member", status: str = "pending",
    ) -> dict[str, Any]:
        async with self._session() as session:
            row = await session.get(WorkspaceMemberOrm, (workspace_id, user_id))
            now = utcnow()
            if row is None:
                # 首次加入：approved 时记 joined_at；否则待审批通过后再记
                row = WorkspaceMemberOrm(
                    workspace_id=workspace_id, user_id=user_id, role=role, status=status,
                    joined_at=now if status == "approved" else None,
                )
                session.add(row)
            else:
                row.role = role
                if row.status != "approved" and status == "approved":
                    row.joined_at = now
                elif status != "approved":
                    row.joined_at = None
                row.status = status
            await session.commit()
            return {
                "workspace_id": row.workspace_id,
                "user_id": row.user_id,
                "role": row.role,
                "status": row.status,
                "joined_at": row.joined_at,
            }

    async def member_get(self, workspace_id: str, user_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(WorkspaceMemberOrm, (workspace_id, user_id))
            if row is None:
                return None
            return {
                "workspace_id": row.workspace_id,
                "user_id": row.user_id,
                "role": row.role,
                "status": row.status,
                "joined_at": row.joined_at,
            }

    async def member_list(self, workspace_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        stmt = select(WorkspaceMemberOrm).where(WorkspaceMemberOrm.workspace_id == workspace_id)
        if status:
            stmt = stmt.where(WorkspaceMemberOrm.status == status)
        stmt = stmt.order_by(WorkspaceMemberOrm.joined_at.desc())
        async with self._session() as session:
            rows = (await session.scalars(stmt)).all()
            return [
                {
                    "workspace_id": r.workspace_id,
                    "user_id": r.user_id,
                    "role": r.role,
                    "status": r.status,
                    "joined_at": r.joined_at,
                }
                for r in rows
            ]

    # ---- feedback ----
    async def feedback_insert(self, rec: dict[str, Any]) -> str:
        data = _to_orm(rec)
        async with self._session() as session:
            session.add(FeedbackOrm(**data))
            await session.commit()
        return rec["id"]

    async def feedback_get(self, fid: str, *, workspace_id: str = "default") -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(FeedbackOrm, fid)
            if row is None or row.workspace_id != workspace_id:
                return None
            return self._row_to_feedback(row)

    async def feedback_list(
        self, *, customer: str | None = None, module: str | None = None,
        limit: int = 100, offset: int = 0, workspace_id: str = "default",
    ) -> list[dict[str, Any]]:
        stmt = select(FeedbackOrm).where(FeedbackOrm.workspace_id == workspace_id)
        if customer:
            stmt = stmt.where(FeedbackOrm.customer == customer)
        if module:
            stmt = stmt.where(FeedbackOrm.module == module)
        stmt = stmt.order_by(FeedbackOrm.created_at.desc()).limit(limit).offset(offset)
        async with self._session() as session:
            rows = (await session.scalars(stmt)).all()
            return [self._row_to_feedback(r) for r in rows]

    async def feedback_count(self, *, workspace_id: str = "default") -> int:
        async with self._session() as session:
            stmt = select(func.count()).select_from(FeedbackOrm).where(
                FeedbackOrm.workspace_id == workspace_id
            )
            return int((await session.scalar(stmt)) or 0)

    @staticmethod
    def _row_to_feedback(row: FeedbackOrm | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "content": row.content,
            "channel": row.channel,
            "customer": row.customer,
            "module": row.module,
            "feedback_type": row.feedback_type,
            "impact": row.impact,
            "source_ref": row.source_ref,
            "submitted_by": row.submitted_by,
            "structured": row.structured or {},
            "created_at": row.created_at,
        }

    # ---- requirement ----
    async def requirement_insert(self, rec: dict[str, Any]) -> str:
        data = _to_orm(rec)
        async with self._session() as session:
            session.add(RequirementOrm(**data))
            await session.commit()
        return rec["id"]

    async def requirement_get(self, rid: str, *, workspace_id: str = "default") -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(RequirementOrm, rid)
            if row is None or row.workspace_id != workspace_id:
                return None
            return self._row_to_requirement(row)

    async def requirement_list(
        self, *, status: str | None = None, priority: str | None = None,
        module: str | None = None, limit: int = 100, offset: int = 0,
        include_archived: bool = False, workspace_id: str = "default",
    ) -> list[dict[str, Any]]:
        stmt = select(RequirementOrm).where(RequirementOrm.workspace_id == workspace_id)
        if not include_archived:
            stmt = stmt.where(RequirementOrm.archived == False)  # noqa: E712
        if status:
            stmt = stmt.where(RequirementOrm.status == status)
        if priority:
            stmt = stmt.where(RequirementOrm.priority == priority)
        if module:
            stmt = stmt.where(RequirementOrm.module == module)
        stmt = stmt.order_by(RequirementOrm.updated_at.desc()).limit(limit).offset(offset)
        async with self._session() as session:
            rows = (await session.scalars(stmt)).all()
            return [self._row_to_requirement(r) for r in rows]

    async def requirement_update(
        self, rid: str, fields: dict[str, Any], *, workspace_id: str = "default",
    ) -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(RequirementOrm, rid)
            if row is None or row.workspace_id != workspace_id:
                return None
            for key, val in fields.items():
                if not hasattr(row, key):
                    continue
                if key == "source_refs":
                    val = _serialize_refs(val)
                setattr(row, key, val)
            await session.commit()
            return self._row_to_requirement(row)

    async def requirement_count(self, *, include_archived: bool = False, workspace_id: str = "default") -> int:
        stmt = select(func.count()).select_from(RequirementOrm).where(
            RequirementOrm.workspace_id == workspace_id
        )
        if not include_archived:
            stmt = stmt.where(RequirementOrm.archived == False)  # noqa: E712
        async with self._session() as session:
            return int((await session.scalar(stmt)) or 0)

    @staticmethod
    def _row_to_requirement(row: RequirementOrm | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "title": row.title,
            "description": row.description,
            "module": row.module,
            "priority": row.priority,
            "status": row.status,
            "feedback_ids": row.feedback_ids or [],
            "source_refs": row.source_refs or [],
            "cluster_id": row.cluster_id,
            "impact_customers": row.impact_customers,
            "similar_feedback_count": row.similar_feedback_count,
            "confidence": row.confidence,
            "tags": row.tags or [],
            "extra": row.extra or {},
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "approved_by": row.approved_by,
            "approved_at": row.approved_at,
            "archived": bool(row.archived),
            "archived_at": row.archived_at,
            "archived_by": row.archived_by,
        }

    # ---- app_meta ----
    async def meta_get(self, key: str) -> Any | None:
        async with self._session() as session:
            row = await session.get(AppMetaOrm, key)
            return row.value if row is not None else None

    async def meta_set(self, key: str, value: Any) -> None:
        async with self._session() as session:
            row = await session.get(AppMetaOrm, key)
            if row is None:
                session.add(AppMetaOrm(key=key, value=value))
            else:
                row.value = value
            await session.commit()

    # ---- stats ----
    async def domain_stats(self) -> dict[str, Any]:
        fb = await self.feedback_count()
        req = await self.requirement_count()
        backend = "postgres" if self._url.startswith("postgresql") else "sqlite"
        # 只暴露 host:port/db，不带密码（旧实现同款脱敏）
        path = self._sqlite_path or self._url.split("@")[-1]
        return {"feedback": fb, "requirement": req, "backend": backend, "path": str(path)}
