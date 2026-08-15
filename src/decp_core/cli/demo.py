# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""演示 CLI：通过数字员工自然语言指令体验完整闭环。

用法：
    decp-demo --instruction "收集反馈并分析，生成需求草稿" \
              --submit "客户 A 导入超过 5000 条订单时失败" \
              --customer "Customer A" --module "批量订单导入"
"""
from __future__ import annotations

import argparse
import asyncio
import json


async def _run(instruction: str, params: dict) -> None:
    from decp_core.agent import DigitalEmployee
    from decp_core.config import settings
    from decp_core.logging_setup import configure_logging

    # 统一日志装配：demo 进程也要挂载滚动文件/SDK 远程上报 handler，
    # 否则 service 层业务打点（feedback.created 等）会静默丢失。
    configure_logging(module_name="decp", level=settings.log_level, settings=settings)

    agent = await DigitalEmployee.create()
    try:
        print(f"🗣  指令: {instruction}")
        print(f"🧭 路由到技能: {agent.route(instruction)}")
        print("─" * 60)
        res = await agent.execute(instruction, **params)
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    finally:
        await agent.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="DECP 数字员工演示")
    parser.add_argument("--instruction", default="收集反馈并分析，生成需求草稿",
                        help="自然语言指令")
    parser.add_argument("--submit", default=None, help="要提交的新反馈内容")
    parser.add_argument("--customer", default=None)
    parser.add_argument("--module", default=None)
    parser.add_argument("--feedback-type", default=None)
    parser.add_argument("--impact", default=None)
    parser.add_argument("--source-ref", default=None)
    parser.add_argument("--decision", choices=["accept", "reject", "merge"], default=None,
                        help="产品经理审核决策")
    parser.add_argument("--requirement-id", default=None, help="审核的目标需求 id")
    parser.add_argument("--reviewer", default="product_manager")
    args = parser.parse_args()

    params: dict = {}
    if args.submit:
        params["submit_feedback"] = args.submit
    for k in ("customer", "module", "feedback_type", "impact", "source_ref"):
        v = getattr(args, k.replace("-", "_"))
        if v:
            params[k] = v
    if args.decision:
        params["decision"] = args.decision
        params["requirement_id"] = args.requirement_id
        params["reviewer"] = args.reviewer

    asyncio.run(_run(args.instruction, params))


if __name__ == "__main__":
    main()
