# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""调用者身份解析：MCP Context 注入 / Skill direct 显式参数 → 统一身份。

MCP 2.0 支持工具函数声明 `ctx: Context | None = None` 参数自动注入
（`Context.request_context.meta` 携带协议/请求元信息）；Skill direct 模式
（`DirectBackend.call(name, arguments)`）则把身份作为显式参数传入。

本模块把两种来源统一解析为 (user_id, workspace_id)，并做成员资格兜底：
- 显式参数 > ctx.meta > 默认身份
- 未传身份时归默认用户 + 默认工作区（存量兼容，单租户行为不变）
"""
from __future__ import annotations

from typing import Any

DEFAULT_USER_ID = "default_user"
DEFAULT_WORKSPACE_ID = "default"


def identity_from_context(ctx: Any | None) -> dict[str, str]:
    """从 MCP Context 提取调用者身份；无 Context / 无 meta 时返回空。"""
    if ctx is None:
        return {}
    try:
        meta = getattr(ctx.request_context, "meta", None) or {}
    except Exception:  # noqa: BLE001 — 非 MCP 调用（测试直调）时 Context 结构不完整
        return {}
    out: dict[str, str] = {}
    uid = getattr(meta, "user_id", None) or (meta.get("user_id") if isinstance(meta, dict) else None)
    wid = getattr(meta, "workspace_id", None) or (meta.get("workspace_id") if isinstance(meta, dict) else None)
    if uid:
        out["user_id"] = uid
    if wid:
        out["workspace_id"] = wid
    return out


def resolve_identity(
    *,
    ctx: Any | None = None,
    user_id: str | None = None,
    workspace_id: str | None = None,
) -> tuple[str, str]:
    """统一身份解析：显式参数 > ctx.meta > 默认身份。

    Returns:
        (user_id, workspace_id)，调用方保证两者均非空。
    """
    meta_id = identity_from_context(ctx)
    uid = user_id or meta_id.get("user_id") or DEFAULT_USER_ID
    wid = workspace_id or meta_id.get("workspace_id") or DEFAULT_WORKSPACE_ID
    return uid, wid


def identity_dict(
    *,
    ctx: Any | None = None,
    user_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, str]:
    """resolve_identity 的 dict 形式（供工具返回值携带身份上下文）。"""
    uid, wid = resolve_identity(ctx=ctx, user_id=user_id, workspace_id=workspace_id)
    return {"user_id": uid, "workspace_id": wid}
