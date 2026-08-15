"""SKILL.md 加载器：从 skills/ 目录读取数字员工技能定义。

DECP 的技能定义遵循外部 Agent Runtime（deerflow）的规范：
SKILL.md（frontmatter: name/description + 正文）+ manifest.json（依赖工具/MCP server）。

本加载器提供：
- 列出/读取技能定义（供外部系统接入、文档、校验）
- 校验技能声明依赖的工具是否已被 DECP MCP server 提供
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# frontmatter: ---\n name: x\n description: y\n ---
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_YAML_PAIR_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")


@dataclass
class SkillDef:
    """一个 SKILL.md 技能定义。"""

    name: str
    description: str
    path: Path
    frontmatter: dict[str, str] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    # manifest.depends_on_tools 或显式 tools 列表
    tools: list[str] = field(default_factory=list)

    @property
    def depends_on_mcp_servers(self) -> list[str]:
        return self.manifest.get("depends_on_mcp_servers", [])

    @property
    def version(self) -> str:
        # AgentScope / Claude Code skill 规范：version 声明在 SKILL.md frontmatter
        return str(self.frontmatter.get("version") or self.manifest.get("version", "0.0.0"))


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 SKILL.md 的 frontmatter，返回 (字段字典, 正文)。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        pair = _YAML_PAIR_RE.match(line.strip())
        if pair:
            fields[pair.group(1)] = pair.group(2).strip().strip("'\"")
    return fields, text[m.end():]


def load_skill_dir(skill_dir: Path) -> SkillDef | None:
    """加载单个技能目录（含 SKILL.md）。"""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    text = skill_md.read_text(encoding="utf-8")
    front, body = parse_frontmatter(text)

    manifest: dict[str, Any] = {}
    mfile = skill_dir / "manifest.json"
    if mfile.is_file():
        try:
            manifest = json.loads(mfile.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            manifest = {}

    tools = list(manifest.get("depends_on_tools", []))
    # frontmatter 或正文声明的工具兜底
    if not tools and front.get("tools"):
        tools = [t.strip() for t in front["tools"].split(",")]
    return SkillDef(
        name=front.get("name", skill_dir.name),
        description=front.get("description", ""),
        path=skill_dir,
        frontmatter=front,
        manifest=manifest,
        tools=tools,
    )


class SkillCatalog:
    """技能目录：扫描 skills/ 目录，提供加载与校验。"""

    def __init__(self, skills_root: str | Path) -> None:
        self.root = Path(skills_root)
        self._skills: dict[str, SkillDef] = {}

    def scan(self) -> list[SkillDef]:
        """扫描 skills/ 下所有含 SKILL.md 的子目录。"""
        self._skills.clear()
        if not self.root.is_dir():
            return []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            if child.name == ".venv":
                continue
            sd = load_skill_dir(child)
            if sd is not None:
                self._skills[sd.name] = sd
        return self.all()

    def get(self, name: str) -> SkillDef | None:
        return self._skills.get(name)

    def all(self) -> list[SkillDef]:
        return list(self._skills.values())

    def missing_tools(self, available: set[str]) -> dict[str, list[str]]:
        """校验技能声明依赖的工具是否可用，返回 技能名 -> 缺失工具。"""
        out: dict[str, list[str]] = {}
        for name, sd in self._skills.items():
            missing = [t for t in sd.tools if t not in available]
            if missing:
                out[name] = missing
        return out
