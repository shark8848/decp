"""存储抽象层：定义存储后端须实现的最小契约。

两种实现：
- SQLiteStorage    -> 零依赖，单文件，开发/演示默认
- PostgresStorage  -> psycopg3 连接池，生产形态

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

    # ---- feedback ----
    @abstractmethod
    async def feedback_insert(self, rec: dict[str, Any]) -> str: ...

    @abstractmethod
    async def feedback_get(self, fid: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def feedback_list(
        self, *, customer: str | None = None, module: str | None = None,
        limit: int = 100, offset: int = 0,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def feedback_count(self) -> int: ...

    # ---- requirement ----
    @abstractmethod
    async def requirement_insert(self, rec: dict[str, Any]) -> str: ...

    @abstractmethod
    async def requirement_get(self, rid: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def requirement_list(
        self, *, status: str | None = None, priority: str | None = None,
        module: str | None = None, limit: int = 100, offset: int = 0,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def requirement_update(self, rid: str, fields: dict[str, Any]) -> dict[str, Any] | None: ...

    @abstractmethod
    async def requirement_count(self) -> int: ...

    # ---- app_meta（版本与 hash 审计） ----
    @abstractmethod
    async def meta_get(self, key: str) -> Any | None: ...

    @abstractmethod
    async def meta_set(self, key: str, value: Any) -> None: ...

    # ---- 数据域完整性 ----
    @abstractmethod
    async def domain_stats(self) -> dict[str, Any]: ...
