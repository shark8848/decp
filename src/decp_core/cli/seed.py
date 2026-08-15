"""种子数据脚本：生成一批贴近真实场景的客户反馈并入库。

用法：
    python -m decp_core.cli.seed [--count 12] [--backend sqlite|postgres]
"""
from __future__ import annotations

import argparse
import asyncio
import random

from decp_core.models import FeedbackCreate
from decp_core.services import FeedbackService

# 真实感反馈模板：(客户, 模块, 类型, 影响, 内容)
_FEEDBACK_TEMPLATES: list[tuple[str, str, str, str, str]] = [
    ("Customer A", "批量订单导入", "性能", "月度结算阻塞",
     "客户 A 导入超过 5000 条订单时失败，影响月度结算。"),
    ("Customer A", "批量订单导入", "性能", "月末出账延期",
     "导入 8000 行订单时页面无响应，月末出账延期两天。"),
    ("Customer B", "ERP 同步", "兼容", "对账中断",
     "ERP 同步时报版本不匹配，无法完成月度对账。"),
    ("Customer B", "ERP 同步", "功能", "数据不同步",
     "ERP 同步新增了字段，接口没有适配，库存数据不同步。"),
    ("Customer C", "登录认证", "功能", "日常作业受阻",
     "客户 C 无法登录系统，提示认证失败，影响日常作业。"),
    ("Customer C", "登录认证", "性能", "频繁掉线",
     "登录后 30 分钟自动掉线，需要重新登录，体验很差。"),
    ("Customer D", "报表导出", "性能", "报表生成超时",
     "导出月度报表超时，3 分钟内无响应，影响管理层查看。"),
    ("Customer D", "报表导出", "功能", "缺少导出格式",
     "报表导出不支持 PDF 格式，客户要求增加。"),
    ("Customer E", "权限管理", "功能", "无法分配权限",
     "无法给新员工分配数据权限，提示角色不存在。"),
    ("Customer E", "权限管理", "安全", "权限越权风险",
     "普通用户能访问管理员数据，存在越权风险。"),
    ("Customer F", "消息通知", "功能", "通知未送达",
     "消息通知经常未送达，客户反馈错过重要提醒。"),
    ("Customer F", "消息通知", "性能", "推送延迟",
     "推送通知延迟超过 1 小时，影响告警时效。"),
    # 真实重复样本：同问题多次上报（用于验证去重）
    ("Customer A", "批量订单导入", "性能", "月度结算阻塞（二次上报）",
     "客户 A 批量导入 5000 条以上订单会失败，导致月度结算无法进行。"),
    ("Customer C", "登录认证", "功能", "认证失败（二次上报）",
     "客户 C 登录一直报认证失败，无法进入系统。"),
]


async def seed(count: int | None = None, backend: str | None = None) -> None:
    from decp_core.config import settings
    from decp_core.storage import create_storage

    if backend:
        settings.storage_backend = backend  # type: ignore[assignment]
    storage = create_storage(settings)
    await storage.connect()
    await storage.init_schema()

    svc = FeedbackService(storage)
    n = count or len(_FEEDBACK_TEMPLATES)
    items = []
    rng = random.Random(42)
    for i in range(n):
        t = _FEEDBACK_TEMPLATES[i % len(_FEEDBACK_TEMPLATES)]
        customer, module, ftype, impact, content = t
        if i >= len(_FEEDBACK_TEMPLATES):
            content = content + f"（重复反馈 {i // len(_FEEDBACK_TEMPLATES) + 1}）"
            impact = impact + "，多次上报"
        items.append(FeedbackCreate(
            content=content, customer=customer, module=module,
            feedback_type=ftype, impact=impact,
            channel=rng.choice(["natural_language", "excel", "ticket"]),
            source_ref=f"TICKET-{1000 + i}",
            submitted_by=rng.choice(["维护人员", "客服", "客户成功"]),
        ))
    created = await svc.create_many(items)
    total = await svc.count()
    print(f"已写入 {len(created)} 条反馈，feedback 数据域共 {total} 条。")
    print("后端:", settings.storage_backend)
    await storage.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="DECP 种子数据")
    parser.add_argument("--count", type=int, default=None, help="反馈条数（默认全部模板）")
    parser.add_argument("--backend", choices=["sqlite", "postgres"], default=None)
    args = parser.parse_args()
    asyncio.run(seed(args.count, args.backend))


if __name__ == "__main__":
    main()
