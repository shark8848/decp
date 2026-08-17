---
name: requirement-query
version: 0.1.0
description: 需求与反馈查询技能。当用户要求查看、查询、搜索客户反馈或需求清单，查看需求状态/优先级/详情，或查找相似反馈时使用本技能。技能通过 DECP 平台的 MCP 工具读取 feedback / requirement 数据域。
---

# 需求与反馈查询技能

本技能面向产品经理与需求收集人员，提供对 DECP 数据域的只读查询能力。不写入任何数据。

## 数据域与工具

| 工具 | 入参要点 | 返回内容 |
|------|---------|---------|
| `feedback.search` | `customer`、`module`、`limit`、`offset` | 反馈列表（最小必要字段） |
| `feedback.get` | `feedback_id` | 单条反馈完整信息 |
| `requirement.search` | `status`、`priority`、`module`、`limit`、`offset`、`include_archived` | 需求列表（默认不含已归档；`include_archived=true` 含） |
| `requirement.get` | `requirement_id` | 需求完整信息（含来源引用、审核记录） |
| `requirement.find_similar` | `text`、`limit` | 与给定文本相似的历史反馈（查重） |
| `domain.stats` | — | 数据域统计（feedback/requirement 数量，当前工作区口径） |
| `workspace.list` | — | 本人所属 workspace 列表 |
| `workspace.get` | `workspace_id` | workspace 详情 |

> **工具名对照**：在 AgentScope 等外部运行时下，工具对 LLM 暴露的实际名称为 `mcp__decp__*` 前缀 + `.` → `x` 的改写（如 `feedback.search` → `mcp__decp__feedbackxsearch`）。上表为业务简化名。**调用前请以当前环境工具列表中的实际名称为准**，勿用简化名直调；若工具列表不含所需工具，先确认 decp MCP server 已挂载。

## 使用方式

## 多工作区隔离（多租户）

DECP 按 workspace 隔离数据，查询只作用于当前身份所属 workspace。身份来源：显式参数 `user_id` / `workspace_id` > 调用上下文 > 默认身份。查询前可先 `workspace.list` 确认归属；工具返回 `WorkspaceError`（非成员）时先处理归属再查询。返回结果携带 `workspace_id` / `user_id` 可确认作用域。

按用户意图选择查询目标：

- **查反馈**：「最近的反馈」「某客户/某模块的反馈」→ `feedback.search`（带过滤参数）。
- **查需求**：「需求清单」「某状态/优先级的需求」→ `requirement.search`（带过滤参数）。
- **查归档需求**：「归档的需求」「查一下已归档的」→ `requirement.search` with `include_archived=true`。
- **查详情**：「REQ-xxx 这个需求怎么样了」→ `requirement.get`。
- **查重**：「这条反馈是不是重复了」→ `requirement.find_similar`（传入待查文本）。
- **概览**：「数据域有多少数据」→ `domain.stats`。
- **归属确认**：「我在哪个工作区/有哪些工作区」→ `workspace.list`。

## 行为约束

- **只读**：本技能不调用任何写入类工具（`feedback.submit` / `requirement.review` / `requirement.archive` / `requirement.restore` 等写操作不在本技能范围）；`include_archived` 仅用于查询。
- **最小必要数据**：优先按 `customer`/`module`/`status`/`priority` 过滤，`limit` 默认 50，避免拉全量。
- **查询结果需转述**：将工具返回的结构化结果整理为可读信息向用户汇报，不直接堆原始 JSON。
