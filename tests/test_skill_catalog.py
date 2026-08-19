"""SkillCatalog 测试：SKILL.md 定义加载与工具依赖校验。"""
from __future__ import annotations

from pathlib import Path

from decp_core.agent.skill_catalog import SkillCatalog, load_skill_dir, parse_frontmatter

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"


def test_parse_frontmatter():
    text = """---
name: demo-skill
description: 演示技能
---
# 正文"""
    front, body = parse_frontmatter(text)
    assert front["name"] == "demo-skill"
    assert front["description"] == "演示技能"
    assert "正文" in body


def test_skill_catalog_scans_decp_skills():
    cat = SkillCatalog(SKILLS_ROOT)
    skills = cat.scan()
    names = {s.name for s in skills}
    assert names == {
        "requirement-analysis", "requirement-query", "feedback-collect", "soul",
        "task-management", "bug-management", "meeting-minutes",
    }


def test_skill_catalog_frontmatter():
    cat = SkillCatalog(SKILLS_ROOT)
    cat.scan()
    analysis = cat.get("requirement-analysis")
    assert analysis is not None
    assert analysis.description.startswith("产品需求收集")
    assert "feedback.submit" in analysis.tools
    assert "decp" in analysis.depends_on_mcp_servers


def test_skill_catalog_missing_tools():
    cat = SkillCatalog(SKILLS_ROOT)
    cat.scan()
    # 全部工具都应被 DECP MCP server 提供
    missing = cat.missing_tools(set(_all_tools()))
    assert missing == {}


def test_skill_catalog_soul_excluded_from_triggers():
    """soul 为注入型技能：不依赖工具/server，不参与可触发技能列表，missing_tools 不校验它。"""
    cat = SkillCatalog(SKILLS_ROOT)
    cat.scan()
    soul = cat.get("soul")
    assert soul is not None
    assert soul.is_injection is True
    # 不在可触发技能之列（不参与意图路由）
    assert soul.name not in {s.name for s in cat.triggers()}
    # 空可用工具集下，注入型技能也不报缺失（depends_on_tools 恒为空）
    missing = cat.missing_tools(set())
    assert "soul" not in missing
    # 其余流程技能均为可触发型
    for name in ("requirement-analysis", "requirement-query", "feedback-collect",
                 "task-management", "bug-management", "meeting-minutes"):
        assert cat.get(name).is_injection is False
        assert cat.get(name).name in {s.name for s in cat.triggers()}


def test_skill_catalog_detects_missing():
    cat = SkillCatalog(SKILLS_ROOT)
    cat.scan()
    missing = cat.missing_tools({"feedback.submit"})  # 只提供一个工具
    assert "requirement-analysis" in missing  # 其余工具缺失
    assert "soul" not in missing  # 注入型技能跳过校验


def _all_tools():
    from decp_core.mcp_.tools import DecpTools

    return list(DecpTools.TOOL_BINDINGS.keys())
