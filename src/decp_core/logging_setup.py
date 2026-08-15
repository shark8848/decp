"""DECP 统一日志装配（集成 ikc-log-center SDK）。

核心 Logger 由 `get_decp_logger(name)` 获取，业务模块不再直接使用
`logging.getLogger` 裸调用；日志经 log_center_sdk 统一装配后，支持：

- 控制台输出（JSON 可选，LOG_JSON）
- 本地滚动文件（LOG_FILE_PATH）
- 远程上报日志中心（LOG_CENTER_ENABLE=true 时 POST {LOG_CENTER_URL}/ingest）

SDK 为可选依赖：未安装 `log-center-sdk` 时自动回落标准库 logging，
DECP 核心功能不受影响（仅失去远程上报能力）。
"""
from __future__ import annotations

import contextvars
import logging
import os
import sys
import uuid
from typing import Any

# ---- 可选导入：log_center_sdk 未安装时回落标准库 --------------------------
try:  # pragma: no cover - 由环境决定
    from log_center_sdk import configure as _sdk_configure
    from log_center_sdk import get_logger as _sdk_get_logger

    _SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    _sdk_configure = None
    _sdk_get_logger = None
    _SDK_AVAILABLE = False

# ---- trace 上下文：DECP 自产 trace_id，满足 ikc-log-center 链路规范 --------
# 上游（MCP 客户端 / HTTP 网关 / 调度方）透传的 trace 标识在此 ContextVar 中，
# 缺失时由 ensure_trace_id() 自产并绑定，保证日志中心每条记录可关联链路。
_trace_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "decp_trace_id", default=None
)

_TRACE_HEADER_NAMES = (
    "x-trace-id", "X-Trace-Id", "trace-id", "x-request-id", "X-Request-Id",
    "x-b3-traceid", "X-B3-TraceId", "sw8", "traceparent",
)


def generate_trace_id() -> str:
    """自产 trace_id：UUID4 十六进制（32 字符，对齐主流 trace 格式）。"""
    return uuid.uuid4().hex


def set_trace_id(trace_id: str) -> None:
    """绑定当前 async context 的 trace_id（DECP ContextVar + SDK contextvars）。"""
    _trace_var.set(trace_id)
    if _SDK_AVAILABLE:
        try:
            from log_center_sdk import set_trace_context

            set_trace_context(trace_id=trace_id)
        except Exception:
            pass


def get_trace_id() -> str | None:
    return _trace_var.get()


def ensure_trace_id(explicit: str | None = None) -> str:
    """返回当前 trace_id；无上游时自产一个并绑定。

    Parameters
    ----------
    explicit:
        上游透传的 trace_id（HTTP header / MCP 元数据 / 调度上下文）。
        仅在当前尚未绑定 trace 时使用，已有绑定不被覆盖。
    """
    current = _trace_var.get()
    if current:
        return current
    tid = (explicit or "").strip() or generate_trace_id()
    set_trace_id(tid)
    return tid


def extract_trace_from_headers(headers: dict[str, str] | None) -> str | None:
    """从上游 header 中提取 trace_id（优先 X-Trace-Id / trace-id 等）。"""
    if not headers:
        return None
    for name in _TRACE_HEADER_NAMES:
        v = headers.get(name)
        if v:
            return str(v).strip()
    return None


class TraceIdFilter(logging.Filter):
    """兜底 Filter：日志发出时若仍未绑定 trace，自产一个并注入 record。

    作用于 get_decp_logger 返回的每个 logger，保证即使调用方绕过
    ensure_trace_id()，日志中心的记录也始终带 trace_id 字段。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        tid = _trace_var.get() or ensure_trace_id()
        record.trace_id = tid
        return True

_configured = False


def configure_logging(
    *,
    module_name: str = "decp",
    level: str | None = None,
    attach_remote: bool | None = None,
    settings: Any | None = None,
) -> None:
    """初始化 DECP 全局日志（幂等，可多次调用）。

    Parameters
    ----------
    module_name:
        日志模块名，用于 per-module 环境变量 override（LOG_CENTER_ENABLE_DECP）
        与默认日志文件名（logs/decp.log）。
    level:
        覆盖 LOG_LEVEL 环境变量；未指定时取 `DECP_LOG_LEVEL` / `LOG_LEVEL`。
    attach_remote:
        是否挂载远程上报 handler。None 表示遵循 config / 环境变量
        （LOG_CENTER_ENABLE，默认 false）；显式 true/false 强制覆盖。
    settings:
        DECP Settings 实例。提供时以其 log_center_* 字段为准，并同步到
        SDK 期望的环境变量（LOG_CENTER_ENABLE / LOG_CENTER_URL / ...）。
    """
    global _configured
    if _configured:
        return
    _configured = True

    if _sdk_configure is not None:
        # config 驱动：将 DECP 配置映射为 SDK 环境变量约定
        if settings is not None:
            _sync_env_from_settings(settings)
        _sdk_configure(
            module_name=module_name,
            level=level,
            attach_remote=attach_remote,
        )
        return

    # ---- 回退路径：标准库滚动文件 handler（SDK 未安装时的本地日志）----------

    # ---- 回落：标准库 basicConfig + 滚动文件（无 SDK 场景）-------------------
    # console 日志走 stderr：stdio 模式下 stdout 是 MCP 协议通道，任何
    # 日志输出到 stdout 都会污染 JSON-RPC 流（与 SDK 路径 StreamHandler()
    # 默认 stderr 行为一致）。
    log_level = (level or _env("DECP_LOG_LEVEL") or _env("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    if settings is not None:
        _attach_rotating_file(logging.getLogger(), settings)


def get_decp_logger(name: str, *, level: str | None = None) -> logging.Logger:
    """获取 DECP 命名空间的 logger（统一前缀 ``decp.``）。

    - SDK 可用时走 sdk_get_logger（可设置 per-logger 级别）。
    - 回落时等价于 logging.getLogger。
    - 每个 logger 挂 TraceIdFilter：确保日志中心记录始终带 trace_id。
    """
    logger_name = name if name.startswith("decp") else f"decp.{name}"
    if _sdk_get_logger is not None:
        logger = _sdk_get_logger(logger_name, level=level)
    else:
        logger = logging.getLogger(logger_name)
        if level:
            logger.setLevel(level.upper())
    # 兜底 trace 注入（幂等：已挂同名 filter 不再重复）
    if not any(isinstance(f, TraceIdFilter) for f in logger.filters):
        logger.addFilter(TraceIdFilter())
    return logger


def _env(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)


def _sync_env_from_settings(settings: Any) -> None:
    """把 DECP Settings 的 log_center_* / 日志滚动字段同步为 SDK 环境变量。

    仅当对应环境变量未显式设置时覆盖，保证环境变量仍为最高优先级。
    """
    mapping = {
        "LOG_CENTER_ENABLE": settings.log_center_enable,
        "LOG_CENTER_URL": settings.log_center_url,
        "LOG_CENTER_DELIVERY": settings.log_center_delivery,
        "LOG_CENTER_TOKEN": settings.log_center_token,
        "LOG_CENTER_TIMEOUT": settings.log_center_timeout,
        # 本地日志滚动（SDK 默认 max_mb=500/backup=3；DECP 收紧防磁盘撑爆）
        "LOG_FILE_ENABLE": settings.log_file_enable,
        "LOG_FILE_PATH": settings.log_file_path,
        "LOG_FILE_MAX_MB": settings.log_file_max_mb,
        "LOG_FILE_BACKUP": settings.log_file_backup,
    }
    for key, value in mapping.items():
        if os.getenv(key) is None and value not in (None, "", False):
            if isinstance(value, bool):
                os.environ[key] = "true" if value else "false"
            else:
                os.environ[key] = str(value)
    if not os.getenv("LOG_CENTER_ENABLE"):
        # 显式 false / 未配置 → 确认关闭远程上报
        os.environ.setdefault("LOG_CENTER_ENABLE", "false")


def _attach_rotating_file(logger: logging.Logger, settings: Any) -> None:
    """回退路径：标准库 RotatingFileHandler 滚动日志（无 SDK 时的本地日志）。

    滚动上限 ≈ log_file_max_mb * log_file_backup（默认 50MB * 5 = 250MB），
    达到即轮转，保证本地磁盘不被日志撑爆。
    """
    if not getattr(settings, "log_file_enable", True):
        return
    path = getattr(settings, "log_file_path", None)
    if not path:
        return
    max_bytes = int(getattr(settings, "log_file_max_mb", 50)) * 1024 * 1024
    backup = int(getattr(settings, "log_file_backup", 5))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    from logging.handlers import RotatingFileHandler

    fh = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup, encoding="utf-8")
    fh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    fh.addFilter(TraceIdFilter())
    logger.addHandler(fh)


__all__ = [
    "configure_logging",
    "get_decp_logger",
    "sdk_available",
    "generate_trace_id",
    "set_trace_id",
    "get_trace_id",
    "ensure_trace_id",
    "extract_trace_from_headers",
    "TraceIdFilter",
]
sdk_available = _SDK_AVAILABLE
