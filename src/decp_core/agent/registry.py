# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT
"""Skill 注册表：管理数字员工拥有的全部技能。"""
from __future__ import annotations

from decp_core.agent.backends import ToolBackend
from decp_core.agent.skills.base import BaseSkill
from decp_core.agent.skills.bug_management import BugManagementSkill
from decp_core.agent.skills.feedback_collect import FeedbackCollectSkill
from decp_core.agent.skills.meeting_minutes import MeetingMinutesSkill
from decp_core.agent.skills.query import QuerySkill
from decp_core.agent.skills.requirement_analysis import RequirementAnalysisSkill
from decp_core.agent.skills.task_management import TaskManagementSkill


class SkillRegistry:
    """技能注册与查询。"""

    def __init__(self, backend: ToolBackend) -> None:
        self._backend = backend
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        self._skills[skill.name] = skill

    def register_defaults(self) -> None:
        self.register(RequirementAnalysisSkill(self._backend))
        self.register(QuerySkill(self._backend))
        self.register(FeedbackCollectSkill(self._backend))
        self.register(TaskManagementSkill(self._backend))
        self.register(BugManagementSkill(self._backend))
        self.register(MeetingMinutesSkill(self._backend))

    def get(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def all(self) -> list[BaseSkill]:
        return list(self._skills.values())

    def names(self) -> list[str]:
        return list(self._skills.keys())

    async def validate_all(self) -> dict[str, list[str]]:
        """校验每个技能的工具依赖是否满足，返回 技能名 -> 缺失工具。"""
        missing: dict[str, list[str]] = {}
        for name, skill in self._skills.items():
            m = await skill.validate()
            if m:
                missing[name] = m
        return missing


def create_registry(backend: ToolBackend) -> SkillRegistry:
    reg = SkillRegistry(backend)
    reg.register_defaults()
    return reg
