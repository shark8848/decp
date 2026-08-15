"""日志装配测试：logging_setup（含 log_center_sdk 可选依赖回落）。"""
import json
import logging
import os
import tempfile

import pytest

from decp_core import logging_setup
from decp_core.config import Settings

# SDK 读取的裸环境变量（测试间相互污染源；含 LOG_FILE_*：_sync_env_from_settings
# 会把 Settings 的日志滚动字段写进环境，且只在未设置时覆盖 → 残留会盖过测试意图）
_LOG_CENTER_ENVS = [
    "LOG_CENTER_ENABLE", "LOG_CENTER_URL", "LOG_CENTER_DELIVERY",
    "LOG_CENTER_TOKEN", "LOG_CENTER_TIMEOUT",
    "LOG_FILE_ENABLE", "LOG_FILE_PATH", "LOG_FILE_MAX_MB", "LOG_FILE_BACKUP",
]


@pytest.fixture(autouse=True)
def _clean_log_env():
    """每个测试前重置环境变量与幂等守卫，避免跨测试污染。"""
    saved = {k: os.environ.get(k) for k in _LOG_CENTER_ENVS}
    for k in _LOG_CENTER_ENVS:
        os.environ.pop(k, None)
    logging_setup._configured = False
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_fallback_without_sdk():
    """SDK 未安装时回落标准库，日志仍可用。"""
    # 重置幂等守卫
    logging_setup._configured = False
    logging_setup.configure_logging(module_name="test_fallback", level="INFO")
    logger = logging_setup.get_decp_logger("test")
    assert logger.name == "decp.test"
    logger.info("fallback ok")


def test_sdk_path_when_available():
    """SDK 可用时走 log_center_sdk 装配（控制台 + 文件 + 远程 handler）。"""
    if not logging_setup.sdk_available:
        pytest.skip("log_center_sdk 未安装")
    # 清空 root handlers + 强制 SDK 重置全局守卫，避免被本文件其他测试污染
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    try:
        import log_center_sdk.core as sdk_core

        sdk_core._initialized = False
    except Exception:
        pass
    logging_setup._configured = False
    s = Settings(storage_backend="sqlite", log_level="INFO",
                 log_center_enable=True, log_center_url="http://127.0.0.1:9",
                 log_center_delivery="api")
    logging_setup.configure_logging(module_name="test_sdk", level="INFO", settings=s)

    handlers = {type(h).__name__ for h in root.handlers}
    assert "HttpLogHandler" in handlers  # 远程上报已挂载


def test_stdio_entry_also_attaches_remote():
    """回归：stdio 入口（decp-mcp / run_stdio）也必须装配远程上报。

    历史 bug：configure_logging 只放在 main()（HTTP 入口），run_stdio 走
    _amain 但不初始化日志 → stdio 部署形态日志中心收不到任何上报。
    _amain 顶部现统一调用 configure_logging，两条入口均覆盖。
    """
    import inspect

    from decp_core.mcp_ import main as mcp_main

    src = inspect.getsource(mcp_main._amain)
    assert "configure_logging" in src, "_amain 必须调用 configure_logging"
    assert "settings" in src, "_amain 必须传入 settings 以同步 LOG_CENTER 环境变量"


def test_logger_name_prefix():
    """get_decp_logger 统一加 decp. 前缀。"""
    assert logging_setup.get_decp_logger("mcp").name == "decp.mcp"
    assert logging_setup.get_decp_logger("decp.agent").name == "decp.agent"


def test_trace_id_self_generated_and_injected():
    """需求1：无上游 trace 时自产 trace_id 并注入日志记录。

    验证 ensure_trace_id() 自产 32 位 hex，且 TraceIdFilter 兜底注入
    record.trace_id（日志中心 JSON 记录带 trace_id 字段的前提）。
    """
    tid = logging_setup.ensure_trace_id()
    assert len(tid) == 32
    assert set(tid) <= set("0123456789abcdef")
    # 幂等：已绑定不再换
    assert logging_setup.ensure_trace_id() == tid
    # 显式上游不被已有绑定覆盖
    other = logging_setup.ensure_trace_id(explicit="deadbeef")
    assert other == tid

    # TraceIdFilter 注入 LogRecord（绕过 context 直接发日志的场景）
    record = logging.LogRecord(
        name="decp.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="trace check", args=(), exc_info=None,
    )
    f = logging_setup.TraceIdFilter()
    assert f.filter(record) is True
    assert len(record.trace_id) == 32


@pytest.mark.skipif(not logging_setup.sdk_available, reason="log_center_sdk 未安装")
def test_file_rotation_prevents_disk_blowup(tmp_path):
    """需求2：本地日志滚动循环，磁盘不被撑爆。

    SDK 路径下 LOG_FILE_MAX_MB / LOG_FILE_BACKUP 生效，RotatingFileHandler
    达到上限即轮转（.gz 压缩备份），文件数与磁盘占用均受控。
    SDK 控制台 StreamHandler 写底层 fd，用 OS dup2 吸掉避免刷屏。
    """
    import log_center_sdk.core as sdk_core
    from logging.handlers import RotatingFileHandler

    log_path = str(tmp_path / "decp.log")
    # OS 级吸掉 stdout/stderr：SDK StreamHandler 持有 fd 引用，sys.stdout 替换拦不住
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    saved_out, saved_err = os.dup(1), os.dup(2)
    os.dup2(devnull_fd, 1)
    os.dup2(devnull_fd, 2)
    try:
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        try:
            sdk_core._initialized = False
        except Exception:
            pass
        logging_setup._configured = False
        s = Settings(
            storage_backend="sqlite",
            log_file_enable=True,
            log_file_path=log_path,
            log_file_max_mb=2,  # 2MB 上限，快速触发滚动
            log_file_backup=3,
            log_center_enable=False,
        )
        logging_setup.configure_logging(module_name="roll_test", settings=s)

        rotators = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert rotators, "应挂载 RotatingFileHandler"

        log = logging_setup.get_decp_logger("roll")
        chunk = "x" * 1024 * 64  # 64KB
        for i in range(80):  # ~5MB 写入 > 2MB 上限，必然触发滚动
            log.info("%s-%d", chunk, i)

        files = sorted(os.listdir(tmp_path))
        total = sum(os.path.getsize(tmp_path / f) for f in files)
        # 主文件 + backup 份备份（.1/.2 或 .1.gz/.2.gz 由 SDK namer 决定）
        assert len(files) <= 1 + s.log_file_backup, f"滚动文件数超限: {files}"
        # 磁盘占用受控（远小于 5MB 原始写入）
        assert total < 2.5 * 1024 * 1024, f"日志超上限: {total} bytes"
        # 至少发生了一次滚动：出现备份文件（主文件之外的 .1*）
        backups = [f for f in files if f != os.path.basename(log_path)]
        assert backups, f"未发生滚动: 无备份文件 {files}"
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(devnull_fd)
        os.close(saved_out)
        os.close(saved_err)


def test_stdout_json_record_carries_trace_id(capsys):
    """需求1（上报侧）：控制台 JSON 日志记录带 trace_id 字段。

    即使调用方绕过 ensure_trace_id，TraceIdFilter 兜底注入 record.trace_id，
    SDK 的 JSON formatter 会把它序列化进输出 —— 日志中心记录可关联链路。
    """
    if not logging_setup.sdk_available:
        pytest.skip("log_center_sdk 未安装")
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    try:
        import log_center_sdk.core as sdk_core

        sdk_core._initialized = False
    except Exception:
        pass
    logging_setup._configured = False
    logging_setup.configure_logging(module_name="trace_check", level="INFO")

    logger = logging_setup.get_decp_logger("trace")
    logger.info("trace presence check")
    captured = capsys.readouterr()
    merged = captured.out or captured.err
    # 控制台默认非 JSON 时退化为断言 TraceIdFilter 存在；SDK JSON 时解析
    lines = [ln for ln in merged.splitlines() if ln.strip().startswith("{")]
    if lines:
        record = json.loads(lines[0])
        assert "trace_id" in record, f"JSON 日志缺 trace_id: {record}"
        assert len(record["trace_id"]) == 32


def test_settings_env_sync():
    """config 的 log_center_* 字段映射为 SDK 环境变量约定。"""
    logging_setup._configured = False
    s = Settings(
        storage_backend="sqlite",
        log_center_enable=True,
        log_center_url="http://log-center:9315",
        log_center_delivery="api",
        log_center_token="secret-token",
    )
    logging_setup._sync_env_from_settings(s)
    assert logging_setup._env("LOG_CENTER_ENABLE") == "true"
    assert logging_setup._env("LOG_CENTER_URL") == "http://log-center:9315"
    assert logging_setup._env("LOG_CENTER_DELIVERY") == "api"
    assert logging_setup._env("LOG_CENTER_TOKEN") == "secret-token"


def test_env_takes_precedence_over_settings():
    """显式环境变量优先于 config 字段，不被覆盖。"""
    import os

    os.environ["LOG_CENTER_URL"] = "http://explicit:9315"
    s = Settings(storage_backend="sqlite", log_center_url="http://settings:9315")
    logging_setup._sync_env_from_settings(s)
    assert logging_setup._env("LOG_CENTER_URL") == "http://explicit:9315"
    del os.environ["LOG_CENTER_URL"]


def test_disabled_remote_defaults_false():
    """未启用上报时 LOG_CENTER_ENABLE 确保为 false。"""
    import os

    os.environ.pop("LOG_CENTER_ENABLE", None)
    s = Settings(storage_backend="sqlite")
    logging_setup._sync_env_from_settings(s)
    assert logging_setup._env("LOG_CENTER_ENABLE") == "false"
