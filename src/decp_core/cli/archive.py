# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""归档命令行：已审核完结需求归档 / 恢复 / 查询。

用法：
    python -m decp_core.cli.archive --archive REQ-xxx [--archived-by 产品经理]
    python -m decp_core.cli.archive --restore REQ-xxx
    python -m decp_core.cli.archive --list [--include-archived]
    python -m decp_core.cli.archive --archive-all-settled [--archived-by 产品经理]
"""
from __future__ import annotations

import argparse
import asyncio
import json

from decp_core.services import RequirementService


async def archive(rid: str, archived_by: str) -> None:
    from decp_core.config import settings
    from decp_core.logging_setup import configure_logging
    from decp_core.storage import create_storage

    configure_logging(module_name="decp", level=settings.log_level, settings=settings)
    storage = create_storage(settings)
    await storage.connect()
    await storage.init_schema()
    try:
        svc = RequirementService(storage)
        req = await svc.archive(rid, archived_by)
        print(json.dumps({"ok": True, "requirement": req.model_dump()}, ensure_ascii=False, indent=2, default=str))
    finally:
        await storage.close()


async def restore(rid: str) -> None:
    from decp_core.config import settings
    from decp_core.logging_setup import configure_logging
    from decp_core.storage import create_storage

    configure_logging(module_name="decp", level=settings.log_level, settings=settings)
    storage = create_storage(settings)
    await storage.connect()
    await storage.init_schema()
    try:
        svc = RequirementService(storage)
        req = await svc.restore(rid)
        print(json.dumps({"ok": True, "requirement": req.model_dump()}, ensure_ascii=False, indent=2, default=str))
    finally:
        await storage.close()


async def list_reqs(include_archived: bool) -> None:
    from decp_core.config import settings
    from decp_core.logging_setup import configure_logging
    from decp_core.storage import create_storage

    configure_logging(module_name="decp", level=settings.log_level, settings=settings)
    storage = create_storage(settings)
    await storage.connect()
    await storage.init_schema()
    try:
        svc = RequirementService(storage)
        items = await svc.list(limit=1000, include_archived=include_archived)
        print(json.dumps({
            "count": len(items),
            "items": [r.model_dump() for r in items],
        }, ensure_ascii=False, indent=2, default=str))
    finally:
        await storage.close()


async def archive_all_settled(archived_by: str) -> None:
    """批量归档全部已审核完结需求（accepted/rejected/merged）。"""
    from decp_core.config import settings
    from decp_core.logging_setup import configure_logging
    from decp_core.storage import create_storage

    configure_logging(module_name="decp", level=settings.log_level, settings=settings)
    storage = create_storage(settings)
    await storage.connect()
    await storage.init_schema()
    try:
        svc = RequirementService(storage)
        items = await svc.list(limit=1000, include_archived=True)
        archived_ids, skipped = [], []
        for r in items:
            if r.archived:
                continue
            if r.status in svc._ARCHIVABLE_STATUS:
                await svc.archive(r.id, archived_by)
                archived_ids.append(r.id)
            else:
                skipped.append({"id": r.id, "status": r.status})
        print(json.dumps({
            "archived": archived_ids,
            "skipped_unsettled": skipped,
        }, ensure_ascii=False, indent=2))
    finally:
        await storage.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="DECP 需求归档命令行")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--archive", metavar="REQ-xxx", help="归档指定需求（须已审核完结）")
    group.add_argument("--restore", metavar="REQ-xxx", help="恢复指定已归档需求")
    group.add_argument("--list", action="store_true", help="列出需求（默认仅活跃）")
    group.add_argument("--archive-all-settled", action="store_true",
                       help="批量归档全部已审核完结需求")
    parser.add_argument("--archived-by", default="maintainer", help="归档人（默认 maintainer）")
    parser.add_argument("--include-archived", action="store_true",
                        help="配合 --list 含已归档需求")
    args = parser.parse_args()

    if args.archive:
        asyncio.run(archive(args.archive, args.archived_by))
    elif args.restore:
        asyncio.run(restore(args.restore))
    elif args.archive_all_settled:
        asyncio.run(archive_all_settled(args.archived_by))
    else:
        asyncio.run(list_reqs(args.include_archived))


if __name__ == "__main__":
    main()
