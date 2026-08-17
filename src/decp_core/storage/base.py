# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""存储抽象层：定义存储后端须实现的最小契约。

实现：
- ORMStorage（SQLAlchemy 2.0 async）-> 单一实现，通过 engine URL 切换
  SQLite（sqlite+aiosqlite://，单文件默认）/ PostgreSQL（postgresql+psycopg://，生产形态）

数据域：feedback（反馈）、requirement（需求）、app_meta（版本/hash 等审计元信息）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator


class StorageBackend(ABC):
    """数据层统一接口。所有 Service 只依赖此接口，不感知具体后端。"""

    # ---- 生命周期 ----
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def init_schema(self) -> None: ...

    # ---- 用户 ----
    @abstractmethod
    async def user_get(self, user_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def user_upsert(self, user_id: str, name: str | None = None) -> dict[str, Any]: ...

    # ---- workspace ----
    @abstractmethod
    async def workspace_insert(self, rec: dict[str, Any]) -> str: ...

    @abstractmethod
    async def workspace_get(self, workspace_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def workspace_list_by_user(self, user_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def member_upsert(
        self, workspace_id: str, user_id: str, *, role: str = "member",
        status: str = "pending",
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def member_get(self, workspace_id: str, user_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def member_list(self, workspace_id: str, *, status: str | None = None) -> list[dict[str, Any]]: ...

    # ---- feedback ----
    @abstractmethod
    async def feedback_insert(self, rec: dict[str, Any]) -> str: ...

    @abstractmethod
    async def feedback_get(self, fid: str, *, workspace_id: str = "default") -> dict[str, Any] | None: ...

    @abstractmethod
    async def feedback_list(
        self, *, customer: str | None = None, module: str | None = None,
        limit: int = 100, offset: int = 0, workspace_id: str = "default",
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def feedback_count(self, *, workspace_id: str = "default") -> int: ...

    # ---- requirement ----
    @abstractmethod
    async def requirement_insert(self, rec: dict[str, Any]) -> str: ...

    @abstractmethod
    async def requirement_get(self, rid: str, *, workspace_id: str = "default") -> dict[str, Any] | None: ...

    @abstractmethod
    async def requirement_list(
        self, *, status: str | None = None, priority: str | None = None,
        module: str | None = None, limit: int = 100, offset: int = 0,
        include_archived: bool = False, workspace_id: str = "default",
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def requirement_update(
        self, rid: str, fields: dict[str, Any], *, workspace_id: str = "default",
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    async def requirement_count(self, *, include_archived: bool = False, workspace_id: str = "default") -> int: ...

    # ---- app_meta（版本与 hash 审计） ----
    @abstractmethod
    async def meta_get(self, key: str) -> Any | None: ...

    @abstractmethod
    async def meta_set(self, key: str, value: Any) -> None: ...

    # ---- 数据域完整性 ----
    @abstractmethod
    async def domain_stats(self) -> dict[str, Any]: ...
