# DECP 白皮书

**DECP · 联邦数字员工协作平台**（Federated Digital Employee Collaboration Platform）

> 将分散的客户反馈转换为 **结构化、去重、可追溯、可审核** 的产品需求，同时保留**人工决策权**与**企业数据主权**。

*版本 0.1.0 · MIT License · © 2026 shark8848*

---

## 1. 问题背景

企业产品团队每天从多渠道接收客户反馈：自然语言、Excel、工单系统、销售会话。这些反馈天然存在三个问题：

- **非结构化** —— 散落在邮件、表格、工单中，无统一 Schema，无法被分析系统消费；
- **重复与噪音** —— 同一问题被不同客户反复上报，真实需求被淹没；
- **不可追溯** —— 从客户原话到正式需求之间缺少来源引用与审核记录。

传统做法依赖人工整理，成本高、周期长、口径不一；完全自动化又难以保留**人工决策权**（产品经理对需求取舍的判断）与**企业数据主权**（客户数据不出域、出站可控）。

## 2. 设计目标

| 目标 | 含义 |
| --- | --- |
| **结构化** | 自由文本 → 标准字段（客户 / 模块 / 类型 / 影响 / 严重度） |
| **去重** | 相似反馈聚合，识别真实需求规模 |
| **可追溯** | 每条正式需求携带来源引用（feedback → requirement） |
| **可审核** | 人工 Review 环节，接受 / 修改 / 合并 / 拆分 / 拒绝 |
| **数据主权** | 客户数据仅在受控网关内流转，出站受权限检查与审计 |

## 3. 场景架构

DECP 按设计文档实现产品需求收集、整理与分析场景的**四层架构**：

> **业务人员**（业务 / 维护 / 产品经理）→ **数字员工**（Agent Runtime）→ **数据访问**（MCP Workspace Gateway）→ **企业数据**（Product Workspace）

```
┌─────────────────────────────────────────────┐
│ 数字员工 Agent / Skill 层（decp_core.agent）   │
│  · 技能：requirement_analysis / query          │
│  · 工具调用双模式：direct（进程内）/ client（跨进程）│
├─────────────────────────────────────────────┤
│ MCP 工具层（decp_core.mcp_）                   │
│  · 13 个 tools：feedback.* / requirement.*     │
│    / report.* / domain.*                      │
│  · 传输：stdio（默认）/ streamable http         │
├─────────────────────────────────────────────┤
│ 企业数据层 Service（decp_core.services）        │
│  · FeedbackService / RequirementService         │
│  · 整理分析：分类/去重/聚类/影响/优先级/来源校验    │
├─────────────────────────────────────────────┤
│ 存储层（decp_core.storage）                    │
│  · StorageBackend 抽象 + SQLite / Postgres     │
└─────────────────────────────────────────────┘
```

## 4. 核心业务闭环（七步）

![核心业务闭环](product-requirement-analysis-scenario_Version2.svg)

| 步骤 | 名称 | 说明 |
| --- | --- | --- |
| 1 | **提交客户反馈** | 自然语言 / Excel / 工单，如「客户 A 导入超过 5000 条订单时失败，影响月度结算」 |
| 2 | **反馈理解与结构化** | 抽取客户 / 模块 / 类型 / 影响，形成标准字段 |
| 3 | **规划数据增强任务** | 制定查询计划：历史反馈、客户工单、已有需求、产品文档 |
| 4 | **AI 整理与分析** | 通过 MCP + Workspace Gateway 受控调用：分类、去重、聚类、影响分析、优先级 |
| 5 | **生成需求草稿** | 依据分析结果生成 REQ 草稿，携带影响客户数 / 优先级 / 相似反馈数 / 置信度 |
| 6 | **产品经理 Review** | 人工审批：接受 / 修改 / 合并 / 拆分 / 拒绝 |
| 7 | **校验并提交正式需求** | Schema Validation → Permission + Conflict Check → Idempotency + Version Commit，写入 Product Workspace |

### 数据源（步骤 3 的可扩展接入）

- **客户反馈库**：Excel / CSV / 表单，非结构化客户反馈
- **工单系统**：客户问题 / 日志摘要 / 处理记录 / 影响范围
- **产品知识库**：产品文档 / 版本说明 / 业务规则 / 模块信息
- **历史需求库**：Requirement Workspace，历史需求 / 状态 / 版本
- **可扩展**：CRM · ERP · 邮件 · 企业 IM · 日志平台 · 研发 Issue · API

### 最终输出

| 输出 | 说明 |
| --- | --- |
| **结构化需求** | 字段完整、Schema 标准化 |
| **分析报告** | 聚类、影响范围、优先级 |
| **产品 Workspace** | 正式入库、版本化管理 |
| **Excel / 报表** | 兼容现有产品管理流程 |

## 5. 技术实现

### 双数据域

- **feedback 域**：客户反馈，结构化抽取后入库存档；
- **requirement 域**：正式需求对象，携带版本 · 来源 · 审批 · 审计。

### 双存储后端

- **SQLite**（默认，零依赖，WAL 模式）—— 开发 / 单机；
- **PostgreSQL**（psycopg3 连接池，JSONB 结构化字段，TIMESTAMPTZ）—— 生产形态。

### 工具调用双模式

- **direct**（进程内）—— 测试 / 演示 / 单进程部署；
- **client**（跨进程，MCP 客户端连接 stdio / http）—— 外部 Agent Runtime 集成。

### 关键设计决策

- **去重 / 聚类**：字符 n-gram 相似度（2-gram / 3-gram Dice + 公共子串），对中文同义改写 / 标点 / 插入鲁棒且确定性；去重阈值 0.28、聚类阈值 0.24（依据真实反馈分布校准）。
- **人工决策保留**：需求正式入库必须经过产品经理 Review（accept / merge / reject），审批人 / 时间落库。
- **来源追溯**：每条需求携带 `source_ref`（反馈 id / 工单号），保证可追溯。
- **出站控制**：MCP Gateway 层做权限检查 · 字段过滤 · 出站控制，客户数据不越域。

## 6. 分发与安装

DECP 以 Python 包与 npm 包双通道分发：

| 通道 | 包名 | 用途 |
| --- | --- | --- |
| PyPI | `decp-core` | 数据层 / 业务集成（`pip install decp-core`） |
| npm | `@shark8848/decp-core` | skills + MCP server 启动器（`npx decp-mcp`） |
| Docker | `decp-core:latest` | 容器化部署（SQLite 单机 / PostgreSQL 生产形态） |

## 7. 与现有 Agent 快速集成

DECP 通过 **标准 MCP 协议**（stdio / HTTP）暴露 13 个数据工具，并以 **SKILL.md** 技能定义描述业务流程，因此可被主流 Agent Runtime 低成本接入。核心集成模式只有两条：

1. **注册 MCP server** → Agent 获得 `feedback.*` / `requirement.*` / `report.*` / `domain.*` 13 个工具；
2. **挂载技能定义**（`skills/*/SKILL.md`）→ Agent 学会「收集反馈 → 分析 → 生成需求」的完整业务编排。

> 技能通过 MCP 工具驱动数据操作，因此先注册 MCP server，再挂载技能（或反之，顺序无关紧要，两者独立）。

### 7.1 启动 MCP server

任选一种方式，保持进程运行即可：

```bash
# 本地（Python）
python -m decp_core.mcp_.main                 # stdio
python -m decp_core.mcp_.main --transport http --port 18100

# 本地（npm，自动准备 venv）
npx decp-setup
npx decp-mcp                                  # stdio
npx decp-mcp --transport http --port 18100

# Docker
docker run --rm -i -v decp-data:/app/data decp-core:latest                                  # stdio
docker run --rm -d -p 18100:18100 -e DECP_MCP_TRANSPORT=http decp-core:latest                # http
```

### 7.2 AgentScope

AgentScope（阿里开源 Agent 框架）通过 `LocalSkillLoader` 加载 DECP 技能、`MCPClient` 连接 MCP server：

```python
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.skill import LocalSkillLoader

# 1) 加载技能定义
loader = LocalSkillLoader("/home/decp/skills")
skills = loader.list_skills()          # feedback-collect / requirement-analysis / requirement-query

# 2) 连接 MCP server（stdio）
client = MCPClient(mcp_config=StdioMCPConfig(
    command="/home/decp/.venv/bin/python",
    args=["-m", "decp_core.mcp_.main"],
))
await client.connect()
tool = client.get_tool("feedback.submit")
result = await tool.call(content="客户 A 导入超过 5000 条订单时失败", customer="Customer A")
await client.close()
```

HTTP 同理：`HttpMCPConfig(url="http://localhost:18100/mcp", is_stateful=True)`。

> 详细验证见 [agentscope-integration.md](agentscope-integration.md)（已实测 13 工具、数据落库）。

### 7.3 deerflow

deerflow（企业级 Agent 编排平台）将 DECP 注册为 **MCP agent exposure**（带 `mcp_token` 鉴权），技能按 workspace 目录加载：

```yaml
# deerflow config/config.yaml
agent_exposures:
  - name: decp-mcp
    agent_name: decp-product-analysis
    description: DECP 产品需求收集、整理与分析
    enabled: true
    require_token: true
    mcp_token: <你的 token>
```

技能：将 DECP `skills/` 目录部署到 deerflow 的 workspace custom skills 目录：

```bash
# deerflow skills 目录约定
skills/tenants/{tenant}/workspaces/{workspace}/custom/
  feedback-collect/       # 复制自 DECP skills/
  requirement-analysis/
  requirement-query/
```

deerflow 的 skill_selector 会扫描该目录，将 SKILL.md 注入 agent 上下文。

### 7.4 Claude Code

Claude Code 通过 `.mcp.json` 注册 MCP server，技能放入 `~/.claude/skills/`：

```bash
# 1) 注册 MCP server
claude mcp add decp -- python -m decp_core.mcp_.main
#   或编辑项目 .mcp.json：
#   {"mcpServers": {"decp": {"command": "npx", "args": ["decp-mcp"], "type": "stdio"}}}

# 2) 安装技能（npm 包自带）
cp -r node_modules/@shark8848/decp-core/skills/* ~/.claude/skills/
```

验证：`claude mcp list` 应看到 decp（13 工具）；在对话中让 Claude 触发技能，如「收集反馈并分析，生成需求草稿」。

### 7.5 Codex

Codex（OpenAI CLI）通过 `config.toml` 的 `[mcp_servers]` 注册：

```toml
# ~/.codex/config.toml
[mcp_servers.decp]
command = "npx"
args = ["decp-mcp"]
# 或本地 Python：
# command = "/home/decp/.venv/bin/python"
# args = ["-m", "decp_core.mcp_.main"]
```

或命令行：`codex mcp add decp -- npx decp-mcp`。重启 Codex 后工具即注入对话上下文。

> 技能定义：Codex 支持 AGENTS.md 描述约定，可将 DECP 技能要点写入项目 `AGENTS.md` 供其引用。

### 7.6 WorkBuddy / 其他 MCP 客户端

WorkBuddy 等基于 MCP 生态的 Agent（含各类 MCP 客户端）统一走标准注册：

```json
{
  "mcpServers": {
    "decp": {
      "command": "npx",
      "args": ["decp-mcp"],
      "type": "stdio"
    }
  }
}
```

远程 HTTP 形态：

```json
{
  "mcpServers": {
    "decp": {
      "url": "http://localhost:18100/mcp",
      "type": "http"
    }
  }
}
```

### 7.7 集成速查表

| Runtime | MCP 注册 | 技能挂载 | 说明 |
| --- | --- | --- | --- |
| **AgentScope** | `MCPClient`（stdio/http） | `LocalSkillLoader("/home/decp/skills")` | 已实测 13 工具 + 落库 |
| **deerflow** | `agent_exposures` + `mcp_token` | workspace `custom/` 目录 | 企业级鉴权 |
| **Claude Code** | `claude mcp add decp` / `.mcp.json` | `~/.claude/skills/` | npm 包自带 skills |
| **Codex** | `[mcp_servers.decp]` / `codex mcp add` | `AGENTS.md` 描述 | OpenAI CLI |
| **WorkBuddy** | `.mcp.json`（stdio/http） | 同 Claude Code | MCP 标准注册 |

### 7.8 验证集成

任何 Runtime 接入后，用同一套冒烟命令验证：

```json
{"method":"initialize","params":{}}                          // 握手
{"method":"tools/list","params":{}}                          // 应返回 13 个工具
{"method":"tools/call","params":{"name":"domain.stats"}}     // 数据域统计
{"method":"tools/call","params":{"name":"feedback.submit","arguments":{"content":"集成测试，导入超时","customer":"集成验证"}}}
```

## 8. 许可

MIT License · © 2026 shark8848 &lt;admin@sharky-ai.com&gt;
