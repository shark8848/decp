# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""存储后端工厂：按配置创建 ORM（SQLite / PostgreSQL）实例。

单一 ORM 实现（SQLAlchemy 2.0 async），通过 engine URL 切换后端方言。
"""
from __future__ import annotations

from urllib.parse import quote

from decp_core.config import Settings
from decp_core.storage.base import StorageBackend
from decp_core.storage.orm_backend import ORMStorage


def build_dsn(settings: Settings) -> str:
    """由配置构造 PostgreSQL DSN。

    用户名/密码经 URL 百分号编码：密码中若含 ``@`` ``#`` ``$`` ``!`` 等
    保留字符（如 ``decp123456$#@!``），直接内联会污染 host/port 解析，
    ``#`` 会被当作 URL fragment 分隔符导致连接失败。
    """
    return (
        f"postgresql+psycopg://{quote(settings.pg_user, safe='')}:"
        f"{quote(settings.pg_password, safe='')}"
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
