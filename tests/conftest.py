"""测试基础设施：共享 SQLite 存储 fixture 与种子反馈。"""
from __future__ import annotations

import pytest
import pytest_asyncio

from decp_core.config import Settings
from decp_core.mcp_.tools import DecpTools
from decp_core.models import FeedbackCreate
from decp_core.services import FeedbackService
from decp_core.storage import create_storage

SEED_FEEDBACKS = [
    ("客户 A 导入超过 5000 条订单时失败，影响月度结算。", "Customer A", "批量订单导入"),
    ("客户 A 导入超过 5000 条订单时失败，影响月度结算，已二次上报。", "Customer A", "批量订单导入"),
    ("ERP 同步时报版本不匹配，无法完成月度对账。", "Customer B", "ERP 同步"),
    ("ERP 同步时提示版本不匹配，导致月度对账无法完成。", "Customer B", "ERP 同步"),
    ("客户 C 无法登录系统，提示认证失败，影响日常作业。", "Customer C", "登录认证"),
    ("客户 C 无法登录系统，认证失败，日常作业受到严重影响。", "Customer C", "登录认证"),
    ("导出月度报表超时，3 分钟内无响应。", "Customer D", "报表导出"),
    ("导出月度报表超时，3 分钟内无响应，影响管理层查看。", "Customer D", "报表导出"),
]


@pytest_asyncio.fixture
async def sqlite_storage(tmp_path):
    s = Settings(
        storage_backend="sqlite",
        sqlite_path=str(tmp_path / "test.db"),
        reports_dir=str(tmp_path / "reports"),
    )
    storage = create_storage(s)
    await storage.connect()
    await storage.init_schema()
    yield storage
    await storage.close()


@pytest_asyncio.fixture
async def feedback_service(sqlite_storage):
    return FeedbackService(sqlite_storage)


@pytest_asyncio.fixture
async def seeded(sqlite_storage):
    """写入一组种子反馈，返回 Feedback 列表。"""
    svc = FeedbackService(sqlite_storage)
    items = [
        FeedbackCreate(content=c, customer=cust, module=mod)
        for c, cust, mod in SEED_FEEDBACKS
    ]
    return await svc.create_many(items)


@pytest_asyncio.fixture
async def decp_tools(sqlite_storage):
    return DecpTools(sqlite_storage, str(sqlite_storage._path.parent / "reports"))


@pytest.fixture
def anyio_backend():
    return "asyncio"
