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
    AttachmentOrm,
    Base,
    BugOrm,
    FeedbackOrm,
    MeetingMinutesOrm,
    RequirementOrm,
    SprintOrm,
    TaskLogOrm,
    TaskOrm,
    UserOrm,
    WorkspaceMemberOrm,
    WorkspaceOrm,
)


def _serialize_refs(refs: Any) -> Any:
    """JSON 列值 → 纯 Python 结构便于 JSON 编码。

    - list（source_refs/feedback_ids/action_items 等）：逐项处理（pydantic 对象→dict、date→ISO）
    - dict（extra/structured 等）：整体递归 _jsonify
    """
    if refs is None:
        return None
    if isinstance(refs, dict):
        return _jsonify(refs)
    if not isinstance(refs, (list, tuple)):
        return refs
    out: list[Any] = []
    for r in refs:
        if hasattr(r, "model_dump"):
            out.append(_jsonify(r.model_dump()))
        elif isinstance(r, dict):
            out.append(_jsonify(r))
        elif isinstance(r, (list, tuple)):
            out.append([_jsonify(v) for v in r])
        else:
            out.append(_jsonify(r))
    return out


def _jsonify(v: Any) -> Any:
    """date/datetime → ISO 字符串；嵌套 dict/list 递归；其余原样。"""
    from datetime import date, datetime as _dt
    if isinstance(v, (date, _dt)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonify(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonify(x) for x in v]
    return v


_JSON_COLUMNS = {
    "source_refs", "feedback_ids", "bug_ids", "requirement_ids", "task_ids",
    "meeting_ids", "labels", "plan_links", "participants", "agenda",
    "decisions", "action_items", "keywords", "extra", "structured",
}


def _to_orm(rec: dict[str, Any]) -> dict[str, Any]:
    """入参 dict → ORM 可写字段（pydantic 对象与 date/datetime 统一转纯 Python 结构）。

    仅实际存在于入参的 JSON 字段才转换，避免为不存在的字段注入无效 keyword。
    """
    out = dict(rec)
    for key in _JSON_COLUMNS:
        if key in out:
            out[key] = _serialize_refs(out.get(key))
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

    # ---- task ----
    async def task_insert(self, rec: dict[str, Any]) -> str:
        data = _to_orm(rec)
        async with self._session() as session:
            session.add(TaskOrm(**data))
            await session.commit()
        return rec["id"]

    async def task_get(self, tid: str, *, workspace_id: str = "default") -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(TaskOrm, tid)
            if row is None or row.workspace_id != workspace_id:
                return None
            return self._row_to_task(row)

    async def task_list(
        self, *, status: str | None = None, type_: str | None = None,
        sprint_id: str | None = None, assignee: str | None = None,
        limit: int = 100, offset: int = 0, include_archived: bool = False,
        workspace_id: str = "default",
    ) -> list[dict[str, Any]]:
        stmt = select(TaskOrm).where(TaskOrm.workspace_id == workspace_id)
        if not include_archived:
            stmt = stmt.where(TaskOrm.archived == False)  # noqa: E712
        if status:
            stmt = stmt.where(TaskOrm.status == status)
        if type_:
            stmt = stmt.where(TaskOrm.type == type_)
        if sprint_id:
            stmt = stmt.where(TaskOrm.sprint_id == sprint_id)
        if assignee:
            stmt = stmt.where(TaskOrm.assignee == assignee)
        stmt = stmt.order_by(TaskOrm.order.asc(), TaskOrm.created_at.desc()).limit(limit).offset(offset)
        async with self._session() as session:
            rows = (await session.scalars(stmt)).all()
            return [self._row_to_task(r) for r in rows]

    async def task_update(
        self, tid: str, fields: dict[str, Any], *, workspace_id: str = "default",
    ) -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(TaskOrm, tid)
            if row is None or row.workspace_id != workspace_id:
                return None
            for key, val in fields.items():
                if not hasattr(row, key):
                    continue
                if key in ("source_refs", "feedback_ids", "bug_ids", "labels", "plan_links"):
                    val = _serialize_refs(val)
                setattr(row, key, val)
            await session.commit()
            return self._row_to_task(row)

    async def task_count(
        self, *, status: str | None = None, include_archived: bool = False,
        workspace_id: str = "default",
    ) -> int:
        stmt = select(func.count()).select_from(TaskOrm).where(
            TaskOrm.workspace_id == workspace_id
        )
        if not include_archived:
            stmt = stmt.where(TaskOrm.archived == False)  # noqa: E712
        if status:
            stmt = stmt.where(TaskOrm.status == status)
        async with self._session() as session:
            return int((await session.scalar(stmt)) or 0)

    async def task_reorder(
        self, tid: str, order: int, *, workspace_id: str = "default",
    ) -> dict[str, Any] | None:
        return await self.task_update(tid, {"order": order}, workspace_id=workspace_id)

    @staticmethod
    def _row_to_task(row: TaskOrm | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "type": row.type,
            "title": row.title,
            "description": row.description,
            "module": row.module,
            "status": row.status,
            "priority": row.priority,
            "assignee": row.assignee,
            "sprint_id": row.sprint_id,
            "planned_start": row.planned_start,
            "due_at": row.due_at,
            "estimate": row.estimate,
            "order": row.order,
            "plan_links": row.plan_links or [],
            "requirement_id": row.requirement_id,
            "feedback_ids": row.feedback_ids or [],
            "bug_ids": row.bug_ids or [],
            "source_refs": row.source_refs or [],
            "labels": row.labels or [],
            "extra": row.extra or {},
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "started_at": row.started_at,
            "done_at": row.done_at,
            "archived": bool(row.archived),
            "archived_at": row.archived_at,
            "archived_by": row.archived_by,
        }

    # ---- bug ----
    async def bug_insert(self, rec: dict[str, Any]) -> str:
        data = _to_orm(rec)
        async with self._session() as session:
            session.add(BugOrm(**data))
            await session.commit()
        return rec["id"]

    async def bug_get(self, bgid: str, *, workspace_id: str = "default") -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(BugOrm, bgid)
            if row is None or row.workspace_id != workspace_id:
                return None
            return self._row_to_bug(row)

    async def bug_get_many(self, ids: list[str], *, workspace_id: str = "default") -> list[dict[str, Any]]:
        if not ids:
            return []
        stmt = select(BugOrm).where(
            BugOrm.workspace_id == workspace_id,
            BugOrm.id.in_(ids),
        )
        async with self._session() as session:
            rows = (await session.scalars(stmt)).all()
            return [self._row_to_bug(r) for r in rows]

    async def bug_list(
        self, *, status: str | None = None, severity: str | None = None,
        priority: str | None = None, assignee: str | None = None,
        module: str | None = None, channel: str | None = None,
        limit: int = 100, offset: int = 0, include_archived: bool = False,
        workspace_id: str = "default",
    ) -> list[dict[str, Any]]:
        stmt = select(BugOrm).where(BugOrm.workspace_id == workspace_id)
        if not include_archived:
            stmt = stmt.where(BugOrm.archived == False)  # noqa: E712
        if status:
            stmt = stmt.where(BugOrm.status == status)
        if severity:
            stmt = stmt.where(BugOrm.severity == severity)
        if priority:
            stmt = stmt.where(BugOrm.priority == priority)
        if assignee:
            stmt = stmt.where(BugOrm.assignee == assignee)
        if module:
            stmt = stmt.where(BugOrm.module == module)
        if channel:
            stmt = stmt.where(BugOrm.channel == channel)
        stmt = stmt.order_by(BugOrm.updated_at.desc()).limit(limit).offset(offset)
        async with self._session() as session:
            rows = (await session.scalars(stmt)).all()
            return [self._row_to_bug(r) for r in rows]

    async def bug_update(
        self, bgid: str, fields: dict[str, Any], *, workspace_id: str = "default",
    ) -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(BugOrm, bgid)
            if row is None or row.workspace_id != workspace_id:
                return None
            for key, val in fields.items():
                if not hasattr(row, key):
                    continue
                if key in ("source_refs", "feedback_ids", "requirement_ids",
                           "task_ids", "meeting_ids", "labels", "plan_links"):
                    val = _serialize_refs(val)
                setattr(row, key, val)
            await session.commit()
            return self._row_to_bug(row)

    async def bug_count(
        self, *, status: str | None = None, include_archived: bool = False,
        workspace_id: str = "default",
    ) -> int:
        stmt = select(func.count()).select_from(BugOrm).where(
            BugOrm.workspace_id == workspace_id
        )
        if not include_archived:
            stmt = stmt.where(BugOrm.archived == False)  # noqa: E712
        if status:
            stmt = stmt.where(BugOrm.status == status)
        async with self._session() as session:
            return int((await session.scalar(stmt)) or 0)

    @staticmethod
    def _row_to_bug(row: BugOrm | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "title": row.title,
            "description": row.description,
            "module": row.module,
            "severity": row.severity,
            "priority": row.priority,
            "status": row.status,
            "channel": row.channel,
            "environment": row.environment,
            "reproduce_steps": row.reproduce_steps,
            "expected": row.expected,
            "actual": row.actual,
            "assignee": row.assignee,
            "reporter": row.reporter,
            "sprint_id": row.sprint_id,
            "due_at": row.due_at,
            "fix_version": row.fix_version,
            "plan_links": row.plan_links or [],
            "feedback_ids": row.feedback_ids or [],
            "requirement_ids": row.requirement_ids or [],
            "task_ids": row.task_ids or [],
            "meeting_ids": row.meeting_ids or [],
            "source_refs": row.source_refs or [],
            "labels": row.labels or [],
            "extra": row.extra or {},
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "fixed_at": row.fixed_at,
            "closed_at": row.closed_at,
            "archived": bool(row.archived),
            "archived_at": row.archived_at,
            "archived_by": row.archived_by,
        }

    # ---- sprint ----
    async def sprint_insert(self, rec: dict[str, Any]) -> str:
        async with self._session() as session:
            session.add(SprintOrm(**rec))
            await session.commit()
        return rec["id"]

    async def sprint_get(self, spid: str, *, workspace_id: str = "default") -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(SprintOrm, spid)
            if row is None or row.workspace_id != workspace_id:
                return None
            return {
                "id": row.id,
                "workspace_id": row.workspace_id,
                "name": row.name,
                "goal": row.goal,
                "start_date": row.start_date,
                "end_date": row.end_date,
                "status": row.status,
                "created_at": row.created_at,
            }

    async def sprint_list(self, *, status: str | None = None, workspace_id: str = "default") -> list[dict[str, Any]]:
        stmt = select(SprintOrm).where(SprintOrm.workspace_id == workspace_id)
        if status:
            stmt = stmt.where(SprintOrm.status == status)
        stmt = stmt.order_by(SprintOrm.end_date.asc())
        async with self._session() as session:
            rows = (await session.scalars(stmt)).all()
            return [
                {
                    "id": r.id,
                    "workspace_id": r.workspace_id,
                    "name": r.name,
                    "goal": r.goal,
                    "start_date": r.start_date,
                    "end_date": r.end_date,
                    "status": r.status,
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    async def sprint_update(
        self, spid: str, fields: dict[str, Any], *, workspace_id: str = "default",
    ) -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(SprintOrm, spid)
            if row is None or row.workspace_id != workspace_id:
                return None
            for key, val in fields.items():
                if not hasattr(row, key):
                    continue
                setattr(row, key, val)
            await session.commit()
            return await self.sprint_get(spid, workspace_id=workspace_id)

    # ---- task_log ----
    async def log_insert(self, rec: dict[str, Any]) -> int:
        async with self._session() as session:
            row = TaskLogOrm(**rec)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return int(row.id)

    async def log_list(
        self, task_id: str, *, entity: str = "task", workspace_id: str = "default",
    ) -> list[dict[str, Any]]:
        stmt = (
            select(TaskLogOrm)
            .where(TaskLogOrm.workspace_id == workspace_id,
                   TaskLogOrm.task_id == task_id,
                   TaskLogOrm.entity == entity)
            .order_by(TaskLogOrm.created_at.asc())
        )
        async with self._session() as session:
            rows = (await session.scalars(stmt)).all()
            return [
                {
                    "id": r.id,
                    "workspace_id": r.workspace_id,
                    "task_id": r.task_id,
                    "entity": r.entity,
                    "action": r.action,
                    "from_status": r.from_status,
                    "to_status": r.to_status,
                    "field": r.field,
                    "old_value": r.old_value,
                    "new_value": r.new_value,
                    "actor": r.actor,
                    "comment": r.comment,
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    # ---- meeting_minutes ----
    async def meeting_insert(self, rec: dict[str, Any]) -> str:
        data = _to_orm(rec)
        async with self._session() as session:
            session.add(MeetingMinutesOrm(**data))
            await session.commit()
        return rec["id"]

    async def meeting_get(self, mid: str, *, workspace_id: str = "default") -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(MeetingMinutesOrm, mid)
            if row is None or row.workspace_id != workspace_id:
                return None
            return self._row_to_meeting(row)

    async def meeting_list(
        self, *, module: str | None = None, participant: str | None = None,
        limit: int = 100, offset: int = 0, include_archived: bool = False,
        workspace_id: str = "default",
    ) -> list[dict[str, Any]]:
        stmt = select(MeetingMinutesOrm).where(MeetingMinutesOrm.workspace_id == workspace_id)
        if not include_archived:
            stmt = stmt.where(MeetingMinutesOrm.archived == False)  # noqa: E712
        if module:
            stmt = stmt.where(MeetingMinutesOrm.module == module)
        stmt = stmt.order_by(MeetingMinutesOrm.held_at.desc())
        async with self._session() as session:
            rows = (await session.scalars(stmt)).all()
        items = [self._row_to_meeting(r) for r in rows]
        if participant:
            # 参与人过滤：Python 层精确匹配。
            # SQLite JSON 列存 \uXXXX 转义，LIKE 对中文失效；PG JSONB 行为不同。
            # 会议数量级小，内存过滤可接受且跨后端一致。
            items = [m for m in items if participant in (m.get("participants") or [])]
        return items[offset:offset + limit]

    async def meeting_update(
        self, mid: str, fields: dict[str, Any], *, workspace_id: str = "default",
    ) -> dict[str, Any] | None:
        async with self._session() as session:
            row = await session.get(MeetingMinutesOrm, mid)
            if row is None or row.workspace_id != workspace_id:
                return None
            for key, val in fields.items():
                if not hasattr(row, key):
                    continue
                if key in ("participants", "agenda", "decisions", "action_items", "keywords"):
                    val = _serialize_refs(val)
                setattr(row, key, val)
            await session.commit()
            return self._row_to_meeting(row)

    async def meeting_count(self, *, include_archived: bool = False, workspace_id: str = "default") -> int:
        stmt = select(func.count()).select_from(MeetingMinutesOrm).where(
            MeetingMinutesOrm.workspace_id == workspace_id
        )
        if not include_archived:
            stmt = stmt.where(MeetingMinutesOrm.archived == False)  # noqa: E712
        async with self._session() as session:
            return int((await session.scalar(stmt)) or 0)

    @staticmethod
    def _row_to_meeting(row: MeetingMinutesOrm | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "title": row.title,
            "held_at": row.held_at,
            "participants": row.participants or [],
            "location": row.location,
            "recording_url": row.recording_url,
            "agenda": row.agenda or [],
            "module": row.module,
            "raw_text": row.raw_text,
            "summary": row.summary,
            "decisions": row.decisions or [],
            "action_items": row.action_items or [],
            "keywords": row.keywords or [],
            "submitted_by": row.submitted_by,
            "source_ref": row.source_ref,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "archived": bool(row.archived),
            "archived_at": row.archived_at,
            "archived_by": row.archived_by,
        }

    # ---- attachment ----
    async def attachment_insert(self, rec: dict[str, Any]) -> str:
        async with self._session() as session:
            session.add(AttachmentOrm(**rec))
            await session.commit()
        return rec["id"]

    async def attachment_list(
        self, entity: str, entity_id: str, *, workspace_id: str = "default",
    ) -> list[dict[str, Any]]:
        stmt = (
            select(AttachmentOrm)
            .where(AttachmentOrm.workspace_id == workspace_id,
                   AttachmentOrm.entity == entity,
                   AttachmentOrm.entity_id == entity_id)
            .order_by(AttachmentOrm.created_at.desc())
        )
        async with self._session() as session:
            rows = (await session.scalars(stmt)).all()
            return [
                {
                    "id": r.id,
                    "workspace_id": r.workspace_id,
                    "entity": r.entity,
                    "entity_id": r.entity_id,
                    "url": r.url,
                    "name": r.name,
                    "mime": r.mime,
                    "size": r.size,
                    "uploaded_by": r.uploaded_by,
                    "created_at": r.created_at,
                }
                for r in rows
            ]
