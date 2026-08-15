# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""DECP 统一配置。

配置来源优先级（高 → 低）：
1. 环境变量（前缀 `DECP_`）
2. `.env` 文件
3. 代码默认值
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_TYPE = Literal["sqlite", "postgres"]
TOOL_BACKEND = Literal["direct", "client"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class Settings(BaseSettings):
    """DECP 全局配置。"""

    model_config = SettingsConfigDict(
        env_prefix="DECP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 数据根目录 ----
    # 显式指定时作为 sqlite_path / reports_dir 的默认基准；
    # 未指定时回落到项目根（源码开发形态）。容器部署须设为 /app/data。
    data_dir: str = str(PROJECT_ROOT / "data")

    # ---- 存储 ----
    # sqlite | postgres
    storage_backend: BACKEND_TYPE = "sqlite"
    # 显式设置 sqlite_path 时优先；否则 data_dir 决定
    sqlite_path: str | None = None
    # PostgreSQL（storage_backend=postgres 时生效）
    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
    pg_db: str = "decp"
    pg_user: str = "postgres"
    pg_password: str = Field(default="", description="PostgreSQL 密码")
    pg_pool_min: int = 1
    pg_pool_max: int = 10

    # ---- 数字员工 Skill 层工具调用模式 ----
    # direct: 进程内直调 MCP 已注册的 tool 函数
    # client: 通过 mcp client 连接独立运行的 decp-mcp server（跨进程）
    skill_tool_backend: TOOL_BACKEND = "direct"
    # client 模式下的 MCP server 启动命令（如 ["decp-mcp"] 或 ["python","-m","decp_core.mcp_.main"]）
    skill_mcp_command: list[str] = ["python", "-m", "decp_core.mcp_.main"]
    skill_mcp_cwd: str = str(PROJECT_ROOT)

    # ---- 报告 ----
    reports_dir: str | None = None

    # ---- 服务 ----
    log_level: str = "INFO"

    # ---- ikc-log-center 远程日志上报（log_center_sdk）----
    # 上报开关：true 时按 log_center_url POST {url}/ingest（SDK 环境变量 LOG_CENTER_ENABLE）
    log_center_enable: bool = False
    # 日志中心 HTTP 地址（如 http://127.0.0.1:9315）
    log_center_url: str = ""
    # 投递方式：api | grpc | celery（逗号分隔多通道）
    log_center_delivery: str = "api"
    # 日志中心 Bearer token（服务端开启认证时必填）
    log_center_token: str = ""
    # 上报超时（秒）
    log_center_timeout: float = 2.0

    # ---- 本地日志滚动（防磁盘撑爆）----
    # 是否启用本地日志文件（默认开启；可设 false 仅控制台）
    log_file_enable: bool = True
    # 本地日志路径（默认 {data_dir}/logs/decp.log）
    log_file_path: str | None = None
    # 单文件上限（MB），达到即滚动
    log_file_max_mb: int = 50
    # 保留滚动文件数（含当前文件，磁盘上限 ≈ max_mb * backup_count）
    log_file_backup: int = 5

    @field_validator("sqlite_path", "reports_dir", "log_file_path", mode="before")
    @classmethod
    def _resolve_data_paths(cls, v: str | None, info) -> str | None:
        """sqlite_path / reports_dir / log_file_path 未显式指定时，基于 data_dir 解析默认值。

        data_dir 显式设置（容器：/app/data）时回落其下；
        未设置时维持旧默认（PROJECT_ROOT/data）——源码开发形态行为不变。
        """
        if v:
            return v
        # data_dir 声明在这些字段之前，validator 运行时已可读到。
        data_dir_raw = (info.data or {}).get("data_dir") or str(PROJECT_ROOT / "data")
        data_root = Path(data_dir_raw)
        fname = info.field_name
        if fname == "sqlite_path":
            return str(data_root / "decp.db")
        if fname == "reports_dir":
            return str(data_root / "reports")
        return str(data_root / "logs" / "decp.log")

    @property
    def reports_path(self) -> Path:
        return Path(self.reports_dir)

    @property
    def sqlite_path_obj(self) -> Path:
        return Path(self.sqlite_path)


settings = Settings()
