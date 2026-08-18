# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""团队任务管理技能（看板/排期/待办/流转/方案链接）。

与 skills/task-management/SKILL.md 对应：
本 Python 类用于**进程内编排验证**（direct 模式），
外部成熟 Agent Runtime 通过读取 SKILL.md 了解流程、通过 MCP 协议调用同一批工具。
"""
from __future__ import annotations

from typing import Any

from decp_core.agent.skills.base import BaseSkill


class TaskManagementSkill(BaseSkill):
    """团队任务管理：看板、排期、流转、技术债/运营任务、需求转开发任务、方案链接。"""

    name = "task_management"
    description = (
        "团队任务管理：查看/管理任务看板、创建任务、排期到迭代（sprint）、"
        "流转任务状态（backlog/todo/in_progress/review/blocked/done）、"
        "将已审核需求转为开发任务、上传方案链接、管理技术债与运营任务。"
        "适用于研发排期、团队协作、任务跟踪的场景。"
    )
    tools_required = [
        "task.create",
        "task.move",
        "task.board",
        "task.list",
        "task.get",
        "task.upload_plan",
        "task.link_requirement",
        "task.link_bug",
        "sprint.create",
        "sprint.list",
        "bug.search",
        "requirement.search",
    ]

    async def run(self, **params: Any) -> Any:
        action = params.get("action", "board")
        result: dict[str, Any] = {"skill": self.name, "action": action}

        # 1) 看板视图
        if action in ("board", "full"):
            result["board"] = await self._call(
                "task.board",
                status=params.get("status"),
                sprint_id=params.get("sprint_id"),
                assignee=params.get("assignee"),
                type=params.get("type"),
            )

        # 2) 创建任务
        if params.get("create_task"):
            result["created"] = await self._call(
                "task.create",
                title=params["create_task"],
                type=params.get("type", "project"),
                priority=params.get("priority"),
                assignee=params.get("assignee"),
                sprint_id=params.get("sprint_id"),
                due_at=params.get("due_at"),
                module=params.get("module"),
                labels=params.get("labels"),
            )

        # 3) 状态流转（看板拖拽）
        if params.get("move_task") and params.get("status"):
            result["moved"] = await self._call(
                "task.move",
                task_id=params["move_task"],
                status=params["status"],
                comment=params.get("comment"),
                order=params.get("order"),
            )

        # 4) 需求转开发任务
        if params.get("link_requirement"):
            result["linked"] = await self._call(
                "task.link_requirement",
                requirement_id=params["link_requirement"],
            )

        # 5) 上传方案链接
        if params.get("upload_plan") and params.get("plan_url"):
            result["plan"] = await self._call(
                "task.upload_plan",
                task_id=params["upload_plan"],
                url=params["plan_url"],
                name=params.get("plan_name"),
            )

        # 6) 迭代排期
        if params.get("create_sprint"):
            result["sprint"] = await self._call(
                "sprint.create",
                name=params["create_sprint"],
                start_date=params.get("start_date"),
                end_date=params.get("end_date"),
                goal=params.get("goal"),
            )
        if action in ("sprints", "full"):
            result["sprints"] = await self._call("sprint.list", status=params.get("sprint_status"))

        # 7) 任务列表 / 详情
        if action == "list":
            result["tasks"] = await self._call(
                "task.list",
                status=params.get("status"),
                type=params.get("type"),
                sprint_id=params.get("sprint_id"),
                assignee=params.get("assignee"),
            )
        if params.get("task_id"):
            result["task"] = await self._call("task.get", task_id=params["task_id"])

        return result
