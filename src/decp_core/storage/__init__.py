"""存储后端工厂：按配置创建 SQLite 或 PostgreSQL 实例。"""
from __future__ import annotations

from decp_core.config import Settings
from decp_core.storage.base import StorageBackend
from decp_core.storage.postgres_backend import PostgresStorage
from decp_core.storage.sqlite_backend import SQLiteStorage


def build_dsn(settings: Settings) -> str:
    """由配置构造 PostgreSQL DSN。"""
    return (
        f"postgresql://{settings.pg_user}:{settings.pg_password}"
        f"@{settings.pg_host}:{settings.pg_port}/{settings.pg_db}"
    )


def create_storage(settings: Settings) -> StorageBackend:
    """按配置创建存储后端实例（未连接；调用方负责 connect + init_schema）。"""
    if settings.storage_backend == "sqlite":
        return SQLiteStorage(settings.sqlite_path_obj)
    if settings.storage_backend == "postgres":
        return PostgresStorage(
            build_dsn(settings),
            pool_min=settings.pg_pool_min,
            pool_max=settings.pg_pool_max,
        )
    raise ValueError(f"未知存储后端: {settings.storage_backend}")
