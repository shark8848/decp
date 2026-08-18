# DECP Skills（数字员工技能定义）

本目录存放 DECP 数字员工面向**产品需求收集、整理与分析**场景的技能定义，供**外部成熟 Agent Runtime**（deerflow 等）加载执行。

## 技能清单

| 技能 | 目录 | 用途 | 依赖工具 |
| --- | --- | --- | --- |
| 需求收集-整理-分析 | [requirement-analysis/](requirement-analysis/SKILL.md) | 完整闭环：反馈 → 分析 → 审核 → 入库 → 归档 | `feedback.*` + `requirement.*` + `report.*` + `workspace.*`（含成员审批） |
| 需求与反馈查询 | [requirement-query/](requirement-query/SKILL.md) | 只读查询：反馈/需求列表、详情、查重、统计 | `feedback.search`/`get`、`requirement.search`/`get`/`find_similar`、`domain.stats`、`workspace.list`/`get`/`create`/`join`/`join_by_passcode` |
| 客户反馈收集 | [feedback-collect/](feedback-collect/SKILL.md) | 录入反馈并结构化（自然语言/工单/Excel） | `feedback.submit`、`workspace.list`/`create`/`join`/`join_by_passcode` |
| 团队任务管理 | [task-management/](task-management/SKILL.md) | 任务看板（backlog/todo/in_progress/review/blocked/done）、迭代排期（sprint）、技术债/运营/事务任务、需求转开发任务、方案链接、缺陷关联 | `task.*` + `sprint.*` + `bug.search` + `requirement.search` + `workspace.*` |
| 缺陷管理 | [bug-management/](bug-management/SKILL.md) | 缺陷全生命周期（new→confirmed→in_progress→fixed→verified→closed/wonfix）、复现信息、多域关联（反馈/需求/任务/会议）、反馈转缺陷、修复方案 | `bug.*` + `task.create` + `workspace.*` |
| 会议纪要管理 | [meeting-minutes/](meeting-minutes/SKILL.md) | 提交纪要原文并结构化提取（摘要/决议/待办/关键词）、待办批量转任务（开发/技术债/运营/事务分类）、纪要缺陷识别、纪要查询与追溯 | `meeting.*` + `task.create` + `bug.create` + `workspace.*` |
| 数字员工人格 | [soul/](soul/SKILL.md) | 人格 / 价值观 / 行为准则：定义数字员工以何种立场工作，与流程技能配合注入 | 无（不触发，仅注入） |

## 目录规范

每个技能一个目录，包含：

```
skills/{skill-name}/
├── SKILL.md       # 技能定义（frontmatter: name/description + 正文指令）
└── manifest.json  # 发布清单（版本 / 依赖工具 / 依赖 MCP server）
```

- **SKILL.md frontmatter**：`name`（技能标识）、`description`（触发描述，LLM 调度依据）。
- **SKILL.md 正文**：数据域工具清单、标准执行流程、参数收集要求、行为约束（人工审批不可绕过 / 最小必要数据 / 来源追踪 / Prompt Injection 防护）。
- **manifest.json**：`depends_on_tools` 声明依赖的 MCP 工具，`depends_on_mcp_servers` 声明依赖的 MCP server（本平台为 `decp`）。`soul` 技能不依赖任何工具，`depends_on_tools` 为空数组，由运行时注入人格/约束，不作为可触发技能。

## 对接方式

外部 Agent Runtime 通过 **MCP 协议**调用 DECP 数据能力：

```
Agent Runtime（成熟系统）
   │ 读取 SKILL.md 理解技能流程
   ▼
DECP MCP server（stdio / streamable http）
   │ 54 个工具（feedback.* / requirement.* / report.* / domain.* / workspace.* /
   │            task.* / bug.* / sprint.* / meeting.* / attachment.*）
   ▼
DECP 数据域（feedback / requirement / task / bug / sprint / meeting_minutes /
            attachment / app_meta，SQLite / PostgreSQL）
```

- Agent 的 skill 调度器加载本目录 SKILL.md；执行时调用 `decp` MCP server 的对应工具。
- `soul` 作为数字员工的人格注入：加载后让 Agent 明确立场与红线，**不参与意图路由**（不在触发技能之列）。
- 人工审批由 `requirement.review` 保证：数字员工只能产出 Draft，正式入库必须有产品经理审核记录。
- 归档由 `requirement.archive` 保证：仅已审核完结需求可归档（draft/reviewing 不可归档），默认查询过滤、可恢复。
- **多工作区隔离**：数据按 workspace 隔离，所有数据工具校验成员资格；技能正文含「多工作区隔离」小节，说明身份解析（显式参数 > ctx.meta > 默认身份）与首次使用前需 `workspace.create`/`workspace.join`/`workspace.join_by_passcode` 确认归属。`workspace.join` 申请的 pending 状态须 owner 通过 `workspace.approve_member`/`workspace.reject_member` 审批；通行证 `passcode` 为高敏感凭证，仅 owner 可见。

## 新增技能

1. 新建 `skills/{skill-name}/` 目录，编写 `SKILL.md`（frontmatter 声明 `name`/`description`）与 `manifest.json`。
2. 如涉及新数据能力，先在 `src/decp_core/mcp_/tools.py` 注册对应 MCP 工具。
3. 技能正文须显式声明：数据域工具、执行流程、参数收集要求、行为约束（缺一不可）。

> 设计对齐：技能的流程与治理约束以 `docs/product-requirement-analysis-scenario_Version2.svg` 为准。

## 多运行时兼容

SKILL.md 遵循 **Claude Code / AgentScope 同源**的技能规范（frontmatter 含 `name`/`version`/`description`，正文为知识包 markdown），可被以下运行时直接消费：

| 运行时 | 加载方式 | 兼容状态 |
| --- | --- | --- |
| **DECP 自身** | `decp_core.agent.skill_catalog.SkillCatalog` 扫描 + 工具依赖校验 | ✅ 已实测 |
| **Claude Code** | 复制到 `.claude/skills/` 全局或项目目录 | ✅ 格式兼容 |
| **AgentScope** | `agentscope.skill.LocalSkillLoader(root, scan_subdir=True)` | ✅ 已实测（2.0.6） |

AgentScope 加载示例（已验证）：

```python
import asyncio
from agentscope.skill import LocalSkillLoader

async def main():
    loader = LocalSkillLoader("/home/decp/skills", scan_subdir=True)
    skills = await loader.list_skills()   # -> [Skill(name, description, dir, markdown, updated_at)]
    for s in skills:
        print(s.name, len(s.markdown))

asyncio.run(main())
```

AgentScope 的 skill 调度模型与 Claude Code 同构：`LocalSkillLoader` 扫描目录解析 SKILL.md，LLM 通过内置 `Skill` 查看器工具按名读取技能正文（知识包），随后调用 DECP MCP 的 54 个工具完成数据操作。详见 [docs/agentscope-integration.md](../docs/agentscope-integration.md)。
