# CLAUDE.md — DECP 项目

> DECP（Federated Digital Employee Collaboration Platform，联邦数字员工协作平台）。
> 面向产品需求收集、整理与分析场景，实现**企业数据层 + MCP 工具层 + 数字员工 Skill 层**三层架构。
> 权威设计输入见「核心文档」。

## 1. 项目定位

| 项 | 约定 |
| --- | --- |
| 项目名 | DECP · Federated Digital Employee Collaboration Platform |
| 包名 | `decp-core`（`src/decp_core/`） |
| 当前阶段 | 已实现核心闭环（数据层 + MCP 层 + Skill 层），Python >= 3.12 |
| 业务场景 | 产品需求收集、整理与分析（反馈 → 分析 → 审核 → 入库） |
| 存储后端 | SQLite（默认，零依赖）+ PostgreSQL（psycopg3，生产形态，**已实测通过**） |

**场景目标**：将分散的客户反馈转换为**结构化、去重、可追溯、可审核**的产品需求，同时保留**人工决策权**与**企业数据主权**。

**数据域范围（本次实现）**：`feedback`（客户反馈） + `requirement`（结构化需求）两个数据域。

## 2. 核心文档

| 文档 | 说明 |
| --- | --- |
| `docs/product-requirement-analysis-scenario_Version2.svg` | 需求场景示意图（权威设计输入） |

## 3. 三层架构

```
┌─────────────────────────────────────────────┐
│ 数字员工 Agent / Skill 层（decp_core.agent）     │
│  · 技能：requirement_analysis / query           │
│  · 工具调用双模式：direct（进程内）/ client（跨进程）│
├─────────────────────────────────────────────┤
│ MCP 工具层（decp_core.mcp_）                    │
│  · 13 个 tools：feedback.* / requirement.*     │
│    / report.* / domain.*                      │
│  · 传输：stdio（默认）/ streamable http          │
├─────────────────────────────────────────────┤
│ 企业数据层 Service（decp_core.services）         │
│  · FeedbackService / RequirementService         │
│  · 整理分析：分类/去重/聚类/影响/优先级/来源校验     │
├─────────────────────────────────────────────┤
│ 存储层（decp_core.storage）                     │
│  · StorageBackend 抽象 + SQLite / Postgres     │
└─────────────────────────────────────────────┘
```

### 3.1 目录结构

```
src/decp_core/
  config/__init__.py       # Settings（DECP_ 前缀环境变量 / .env）
  models/__init__.py       # Feedback / Requirement / AnalysisResult 等
  storage/
    base.py                # StorageBackend 抽象接口
    sqlite_backend.py      # SQLite 实现（默认，WAL）
    postgres_backend.py    # PostgreSQL 实现（psycopg3 连接池，JSONB）
    __init__.py            # create_storage() 工厂
  services/__init__.py     # FeedbackService / RequirementService（业务核心）
  report/                  # ReportService：HTML（jinja2）+ Excel（openpyxl）
  mcp_/
    main.py                # MCP server 入口（stdio/http）
    tools.py               # DecpTools：13 个工具注册
    utils.py               # tool_result / error_result（CallToolResult 构造）
  agent/
    backends.py            # DirectBackend / ClientBackend（双模式）
    registry.py            # SkillRegistry
    skills/
      base.py              # BaseSkill（_call 解包逻辑）
      requirement_analysis.py  # 需求收集-整理-分析技能
      query.py             # 查询技能
    __init__.py            # DigitalEmployee（意图路由 + 技能调度）
  cli/                     # seed.py（种子数据）/ demo.py（演示）
tests/                     # pytest 套件（21 passed，含 PG 后端）
data/                      # 运行时数据（db、reports）
scripts/                   # 启动脚本
```

## 4. 核心业务流程（七步，与设计文档对应）

1. **提交客户反馈** — `feedback.submit`：自然语言/Excel/工单，自动结构化抽取
   （启发式：影响严重度、数字量级、问题类型关键词）。
2. **反馈理解与结构化** — 抽取客户/模块/类型/影响，存 `feedback.structured`。
3. **规划数据增强任务** — 当前为数据域内查询（历史反馈/需求），可扩展外部数据源。
4. **AI 整理与分析** — `requirement.analyze`：分类、去重、聚类、影响分析、优先级建议（P0-P3）、来源校验。
5. **生成需求草稿** — `requirement.generate_draft`：REQ-xxx，携带影响客户数/优先级/相似反馈数/置信度/状态/来源引用。
6. **产品经理 Review** — `requirement.review`：accept（接受）/ reject（拒绝）/ merge（合并）；人工审批，版本递增。
7. **校验并提交正式需求** — `requirement.create`：Schema 校验 + 版本化入库 + 审批记录。

## 5. MCP 工具清单（13 个）

| 数据域 | 工具 | 说明 |
| --- | --- | --- |
| feedback | `feedback.submit` | 提交反馈 + 结构化 |
| feedback | `feedback.search` / `feedback.get` | 按客户/模块查询 |
| requirement | `requirement.analyze` | 分类/去重/聚类/影响/优先级/来源校验 |
| requirement | `requirement.generate_draft` | 生成需求草稿（Draft） |
| requirement | `requirement.create` | 正式写入（版本化入库） |
| requirement | `requirement.review` | 审核：accept/reject/merge |
| requirement | `requirement.find_similar` | 相似反馈查重 |
| requirement | `requirement.search` / `get` | 需求查询 |
| report | `report.generate_html` / `generate_excel` | 报告导出（可下载） |
| domain | `domain.stats` | 数据域统计 |

**工具返回约定**：统一返回 `mcp.types.CallToolResult`，含文本摘要（content）与结构化内容（structured_content）；`MCPServer.convert_result` 对 CallToolResult 原样透传，MCP client 与进程内直调行为一致。

**工具名与实现统一**：`DecpTools.TOOL_BINDINGS` 定义 13 个标准工具名 → 方法映射，`register_all_tools`（MCP 层）与 `DirectBackend`（Skill direct 模式）共用，保证跨模式命名一致。

## 6. 数字员工 Skill 层

### 双模式工具调用（`agent/backends.py`）
- **direct**（默认）：进程内直调 `DecpTools` 方法，无协议开销，测试/单进程部署。
- **client**：通过 `mcp.client.stdio` 连接独立运行的 `decp-mcp` server，贴合真实 agent-MCP 部署形态。
- 配置：`DECP_SKILL_TOOL_BACKEND`，同一 `ToolBackend` 接口，Skill 代码无感知切换。

### 技能
| 技能 | 触发场景（自然语言） | 依赖工具 |
| --- | --- | --- |
| `requirement_analysis` | 收集反馈并分析、生成需求草稿、生成报告 | feedback.submit/analyze/generate_draft/review/search + report.* |
| `query` | 查看最近的反馈、查询需求状态 | feedback.search + requirement.search/get/find_similar |

### 意图路由
`DigitalEmployee.route()` 按关键词确定性路由到技能；`execute(指令)` 返回 `{skill, matched_by, result}`。接入 LLM 时可将 `skill.description` 作为工具描述交给模型选择，接口不变。

## 7. 存储层

- **StorageBackend 抽象**：feedback / requirement / app_meta 三个数据域的 CRUD + `domain_stats`。
- **SQLite**：默认，WAL 模式，`data/decp.db`。
- **PostgreSQL**：psycopg3 `AsyncConnectionPool`，JSONB 存结构化字段，TIMESTAMPTZ 存时间。
- **创建方式**：`create_storage(settings)` → `await storage.connect()` → `await storage.init_schema()`。
- 两个后端共享同一 service 逻辑（用存储后端的 SQL 差异封装在后端内部）。

## 8. 配置

```bash
# 存储后端
DECP_STORAGE_BACKEND=sqlite          # sqlite | postgres
DECP_SQLITE_PATH=/home/decp/data/decp.db
# PostgreSQL（postgres 时）
DECP_PG_HOST / DECP_PG_PORT / DECP_PG_DB / DECP_PG_USER / DECP_PG_PASSWORD
# Skill 工具后端
DECP_SKILL_TOOL_BACKEND=direct       # direct | client
DECP_SKILL_MCP_COMMAND=["python","-m","decp_core.mcp_.main"]
# 报告目录 / 日志
DECP_REPORTS_DIR=...  DECP_LOG_LEVEL=INFO
```

配置来源优先级：**环境变量 > .env > 默认值**。完整示例见 `.env.example`。

## 9. 运行方式

```bash
. .venv/bin/activate
pip install -e .            # 安装（依赖 mcp/openpyxl/psycopg/psycopg-pool/jinja2）

# 1) 启动 MCP server（stdio，供 MCP 客户端连接）
python -m decp_core.mcp_.main
# 2) 生成种子数据
python -m decp_core.cli.seed --count 14
# 3) 数字员工演示（自然语言指令）
python -m decp_core.cli.demo --instruction "收集反馈并分析，生成需求草稿" \
  --submit "客户 G 无法导出月度结算报表" --customer "Customer G" --module "报表导出"

# 测试（PG 后端测试读取 .env / 环境变量的 DECP_PG_* 配置，未配置则自动跳过）
python -m pytest tests/ -q

# 切换 PostgreSQL 后端（.env 已配置 decp 用户凭据）
DECP_STORAGE_BACKEND=postgres python -m decp_core.mcp_.main
```

## 10. 关键设计决策与约定

- **中文相似度算法**：字符 2-gram Dice(0.5) + 3-gram Dice(0.2) + 公共子串包含(0.3)。词级方法对中文分词敏感，字符 n-gram 对同义改写/标点/插入更鲁棒且确定性。去重阈值 0.28、聚类阈值 0.24（依据真实反馈分布校准）。
- **人工审批不可绕过**：生成物只能是草稿（draft），正式入库必须有 `approved_by/approved_at` 审核记录。
- **工具名统一**：任何新增工具必须同时登记到 `DecpTools.TOOL_BINDINGS`（MCP 层 + direct 后端共用）。
- **结果可下载**：报告输出到 `data/reports/`，HTML/Excel 均本地文件，供 agent 返回路径。
- 新增数据域/数据源须显式说明：泳道、流程、治理约束（身份委托/最小权限/出站控制/审计）。
