# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""工作区命令行：创建 / 加入 / 审批 / 拒绝 / 列表 / 详情 / 成员。

用法：
    python -m decp_core.cli.workspace create --name "AI 产品" --owner alice [--description "..."]
    python -m decp_core.cli.workspace join --workspace-id WS-xxx --user bob
    python -m decp_core.cli.workspace approve --workspace-id WS-xxx --user bob --approver alice
    python -m decp_core.cli.workspace reject --workspace-id WS-xxx --user carol --approver alice
    python -m decp_core.cli.workspace list --user alice
    python -m decp_core.cli.workspace get --workspace-id WS-xxx --user alice
    python -m decp_core.cli.workspace members --workspace-id WS-xxx --user alice
"""
from __future__ import annotations

import argparse
import asyncio
import json

from decp_core.services import WorkspaceError, WorkspaceService


async def _run(coro):
    from decp_core.config import settings
    from decp_core.logging_setup import configure_logging
    from decp_core.storage import create_storage

    configure_logging(module_name="decp", level=settings.log_level, settings=settings)
    storage = create_storage(settings)
    await storage.connect()
    await storage.init_schema()
    try:
        svc = WorkspaceService(storage)
        return await coro(svc)
    finally:
        await storage.close()


def _dump(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


async def create(name: str, owner: str, description: str) -> None:
    def _do(svc):
        return svc.create(name, owner, description)
    try:
        ws = await _run(_do)
        _dump({"ok": True, "workspace": ws})
    except WorkspaceError as e:
        _dump({"ok": False, "error": str(e)})


async def join(workspace_id: str, user: str) -> None:
    def _do(svc):
        return svc.join(workspace_id, user)
    try:
        m = await _run(_do)
        _dump({"ok": True, "workspace_id": workspace_id, "user_id": user, "status": m["status"]})
    except WorkspaceError as e:
        _dump({"ok": False, "error": str(e)})


async def approve(workspace_id: str, user: str, approver: str) -> None:
    def _do(svc):
        return svc.approve_member(workspace_id, user, approver)
    try:
        m = await _run(_do)
        _dump({"ok": True, "workspace_id": workspace_id, "user_id": user, "status": m["status"]})
    except WorkspaceError as e:
        _dump({"ok": False, "error": str(e)})


async def reject(workspace_id: str, user: str, approver: str) -> None:
    def _do(svc):
        return svc.reject_member(workspace_id, user, approver)
    try:
        m = await _run(_do)
        _dump({"ok": True, "workspace_id": workspace_id, "user_id": user, "status": m["status"]})
    except WorkspaceError as e:
        _dump({"ok": False, "error": str(e)})


async def list_for_user(user: str) -> None:
    def _do(svc):
        return svc.list(user)
    items = await _run(_do)
    _dump({"ok": True, "user_id": user, "count": len(items), "workspaces": items})


async def get(workspace_id: str, user: str) -> None:
    def _do(svc):
        return svc.get(workspace_id, user)
    try:
        ws = await _run(_do)
        _dump({"ok": True, "workspace": ws})
    except WorkspaceError as e:
        _dump({"ok": False, "error": str(e)})


async def members(workspace_id: str, user: str) -> None:
    def _do(svc):
        return svc.members(workspace_id, user)
    try:
        ms = await _run(_do)
        _dump({"ok": True, "workspace_id": workspace_id, "members": ms})
    except WorkspaceError as e:
        _dump({"ok": False, "error": str(e)})


def main() -> None:
    parser = argparse.ArgumentParser(description="DECP 工作区（多租户隔离）命令行")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create", help="创建 workspace，owner 自动成为已批准成员")
    p.add_argument("--name", required=True, help="工作区名称")
    p.add_argument("--owner", required=True, help="创建者用户 id")
    p.add_argument("--description", default="", help="工作区描述")

    p = sub.add_parser("join", help="申请加入 workspace（pending，等待 owner 审批）")
    p.add_argument("--workspace-id", required=True)
    p.add_argument("--user", required=True, help="申请者用户 id")

    p = sub.add_parser("approve", help="owner 审批通过成员加入")
    p.add_argument("--workspace-id", required=True)
    p.add_argument("--user", required=True, help="被批准用户 id")
    p.add_argument("--approver", required=True, help="审批者（须为 owner）")

    p = sub.add_parser("reject", help="owner 拒绝成员加入申请")
    p.add_argument("--workspace-id", required=True)
    p.add_argument("--user", required=True, help="被拒绝用户 id")
    p.add_argument("--approver", required=True, help="审批者（须为 owner）")

    p = sub.add_parser("list", help="我的 workspace 列表")
    p.add_argument("--user", required=True)

    p = sub.add_parser("get", help="workspace 详情（仅本人 workspace 可查）")
    p.add_argument("--workspace-id", required=True)
    p.add_argument("--user", required=True)

    p = sub.add_parser("members", help="成员列表（仅本人 workspace 可查）")
    p.add_argument("--workspace-id", required=True)
    p.add_argument("--user", required=True)

    args = parser.parse_args()
    if args.command == "create":
        asyncio.run(create(args.name, args.owner, args.description))
    elif args.command == "join":
        asyncio.run(join(args.workspace_id, args.user))
    elif args.command == "approve":
        asyncio.run(approve(args.workspace_id, args.user, args.approver))
    elif args.command == "reject":
        asyncio.run(reject(args.workspace_id, args.user, args.approver))
    elif args.command == "list":
        asyncio.run(list_for_user(args.user))
    elif args.command == "get":
        asyncio.run(get(args.workspace_id, args.user))
    elif args.command == "members":
        asyncio.run(members(args.workspace_id, args.user))


if __name__ == "__main__":
    main()
