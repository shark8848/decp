"""存储后端工厂：按配置创建 ORM（SQLite / PostgreSQL）实例。

单一 ORM 实现（SQLAlchemy 2.0 async），通过 engine URL 切换后端方言。
"""
from __future__ import annotations

from decp_core.config import Settings
from decp_core.storage.base import StorageBackend
from decp_core.storage.orm_backend import ORMStorage


def build_dsn(settings: Settings) -> str:
    """由配置构造 PostgreSQL DSN。"""
    return (
        f"postgresql+psycopg://{settings.pg_user}:{settings.pg_password}"
        f"@{settings.pg_host}:{settings.pg_port}/{settings.pg_db}"
    )


def create_storage(settings: Settings) -> StorageBackend:
    """按配置创建存储后端实例（未连接；调用方负责 connect + init_schema）。"""
    if settings.storage_backend == "sqlite":
        return ORMStorage(
            f"sqlite+aiosqlite:///{settings.sqlite_path_obj}",
            sqlite_path=str(settings.sqlite_path_obj),
        )
    if settings.storage_backend == "postgres":
        return ORMStorage(
            build_dsn(settings),
            pool_min=settings.pg_pool_min,
            pool_max=settings.pg_pool_max,
        )
    raise ValueError(f"未知存储后端: {settings.storage_backend}")
