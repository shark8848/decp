# DECP · 联邦数字员工协作平台

Federated Digital Employee Collaboration Platform

> 将分散的客户反馈转换为 **结构化、去重、可追溯、可审核** 的产品需求，同时保留**人工决策权**与**企业数据主权**。

核心业务闭环：`反馈 → 分析 → 审核 → 入库`

权威设计输入：[docs/product-requirement-analysis-scenario_Version2.svg](docs/product-requirement-analysis-scenario_Version2.svg)

## 当前实现范围

按设计文档实现 **产品需求收集、整理与分析** 场景的前三层：

```
业务人员  → 产品经理 / 维护人员（通过自然语言指令与数字员工交互）
数字员工  → decp_core.agent（Skill 层：需求分析 / 查询）
数据访问  → decp_core.mcp_（MCP 工具层，Gateway 语义）
企业数据  → decp_core.storage（product workspace：feedback / requirement 双数据域）
```

**三层均为可实现代码**；存储支持 SQLite（默认，开发）与 PostgreSQL（生产形态）。

## 快速开始

```bash
# 安装
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 写入种子反馈数据（12 条贴近真实场景）
python -m decp_core.cli.seed

# 通过数字员工自然语言指令体验完整闭环
decp-demo --instruction "收集反馈并分析，生成需求草稿"
decp-demo --instruction "查看最近的反馈"
decp-demo --instruction "生成报告"
```

## 架构

### 分层

| 层 | 模块 | 说明 |
| --- | --- | --- |
| 数字员工 | `decp_core.agent` | Skill 层：`feedback_collect`（录入）、`requirement_analysis`（需求收集-整理-分析闭环）、`query`（查询）；`SkillCatalog` 扫描 `skills/` 定义。意图路由 → Skill → MCP 工具 |
| MCP 工具层 | `decp_core.mcp_` | 13 个工具，按 `feedback.*` / `requirement.*` / `report.*` / `domain.*` 组织，Gateway 语义：权限检查 · 字段过滤 · 出站控制 |
| 企业数据层 | `decp_core.services` | 业务逻辑：结构化抽取、去重（文本相似度）、聚类、影响分析、优先级建议、来源校验、需求草稿、审核入库 |
| 存储层 | `decp_core.storage` | 统一 `StorageBackend` 接口；SQLite / PostgreSQL 双实现；版本与 hash 审计（app_meta） |
| 报告 | `decp_core.report` | HTML 分析报告 + Excel 报表（需求清单 / 反馈明细 / 聚类） |

### 工具清单

| 工具 | 说明 |
| --- | --- |
| `feedback.submit` | 提交客户反馈（自然语言/工单/Excel 行），完成结构化抽取 |
| `feedback.search` / `feedback.get` | 查询反馈列表 / 单条详情 |
| `requirement.analyze` | 整理与分析：分类、去重、聚类、影响分析、优先级建议、来源校验 |
| `requirement.generate_draft` | 生成需求草稿（REQ-xxx，状态 Draft，携带来源引用/置信度/影响客户数） |
| `requirement.create` | 正式写入需求对象（版本化入库） |
| `requirement.review` | 产品经理审核：accept / reject / merge（人工审批，版本递增） |
| `requirement.find_similar` | 查找相似反馈（查重入口） |
| `requirement.search` / `requirement.get` | 查询需求列表 / 详情 |
| `report.generate_html` / `report.generate_excel` | 生成可下载的分析报告 / Excel 报表 |
| `domain.stats` | 数据域统计 |

### 数字员工 Skill

Skill 定义存放于 `skills/` 目录（SKILL.md + manifest.json），遵循 **Claude Code / AgentScope 同源**的技能规范，可被多运行时加载。

| Skill | 触发示例 |
| --- | --- |
| `feedback-collect` | 「录入一条客户反馈」「登记客户反馈」 |
| `requirement-analysis` | 「收集反馈并分析，生成需求草稿」「生成报告」 |
| `requirement-query` | 「查看最近的反馈」「这个需求怎么样了」 |

Skill 层支持两种工具调用后端（`DECP_SKILL_TOOL_BACKEND`）：
- `direct`（默认）：进程内直调 MCP 工具函数，适合测试/演示/单进程部署
- `client`：通过 mcp client（stdio）连接独立运行的 MCP server，真实 agent-MCP 部署形态

`decp_core.agent.skill_catalog.SkillCatalog` 从 `skills/` 读取技能定义并校验其依赖的 MCP 工具是否可用；`DigitalEmployee` 提供意图路由 → Skill → MCP 工具的进程内编排。

### 多运行时兼容

| 运行时 | 加载方式 | 状态 |
| --- | --- | --- |
| **DECP 自身** | `SkillCatalog` 扫描 + 工具依赖校验 | ✅ 已实测 |
| **Claude Code** | 复制到 `.claude/skills/` | ✅ 格式兼容 |
| **AgentScope** | `LocalSkillLoader(root, scan_subdir=True)` 加载 + `MCPClient` 连接 DECP MCP | ✅ 已实测（2.0.6），端到端落库通过 |

详见 [docs/agentscope-integration.md](docs/agentscope-integration.md)。

## 日志与远程上报（ikc-log-center）

DECP core 使用统一日志装配（`decp_core.logging_setup`），业务代码通过
`get_decp_logger(name)` 获取 `decp.*` 命名空间的 logger，经
[ikc-log-center](https://pypi.org/project/ikc-log-center) SDK 装配后支持：
控制台（JSON 可选）、本地滚动文件、远程上报日志中心（HTTP POST `{url}/ingest`）。

```bash
# 安装 SDK（可选，未安装自动回落标准库日志）
pip install -e ".[logging]"

# 启用远程上报（环境变量）
export DECP_LOG_CENTER_ENABLE=true
export DECP_LOG_CENTER_URL=http://127.0.0.1:9315
export DECP_LOG_CENTER_DELIVERY=api        # api | grpc | celery
export DECP_LOG_CENTER_TOKEN=your-token    # 服务端开启认证时必填
```

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `DECP_LOG_CENTER_ENABLE` | `false` | 远程上报开关 |
| `DECP_LOG_CENTER_URL` | — | 日志中心地址（POST `{url}/ingest`） |
| `DECP_LOG_CENTER_DELIVERY` | `api` | 投递通道 |
| `DECP_LOG_CENTER_TOKEN` | — | Bearer token |
| `DECP_LOG_CENTER_TIMEOUT` | `2.0` | 上报超时（秒） |
| `DECP_LOG_LEVEL` | `INFO` | 日志级别 |
| `DECP_LOG_FILE_ENABLE` | `true` | 本地滚动文件日志开关 |
| `DECP_LOG_FILE_PATH` | `{data_dir}/logs/decp.log` | 本地日志文件路径 |
| `DECP_LOG_FILE_MAX_MB` | `50` | 单文件上限（MB），达上限即轮转 |
| `DECP_LOG_FILE_BACKUP` | `5` | 滚动备份份数 |

**trace 链路**：每条日志记录自带 `trace_id`。上游透传
（`x-trace-id`/`trace-id`/`x-request-id`/`x-b3-traceid`/`sw8`/`traceparent` 等 header）
优先采用；无上游时 `ensure_trace_id()` 自产 32 位 hex 并绑定到
contextvar（async 安全），`TraceIdFilter` 兜底注入 —— 上报日志中心的 JSON
记录恒含 `trace_id` 字段，可跨系统关联链路。HTTP 传输下（`--transport http`），
每个请求经 `TraceContextMiddleware` 提取上游 `X-Trace-Id` 头绑定到该请求的
contextvar，使整条请求链的日志共享同一 trace_id，可按 trace 还原完整调用链。

**业务日志打点（`decp.service`）**：Service 层关键业务方法产出结构化日志，
供日志中心按事件检索与链路追踪：

| 事件 | 触发 | 关键字段 |
| --- | --- | --- |
| `feedback.created` | 提交单条反馈 | `id/channel/customer/module/type/severity` |
| `feedback.bulk_created` | 批量导入（Excel/CSV） | `count/ids` |
| `requirement.analyzed` | 整理与分析 | `feedbacks/categories/dup_groups/clusters/prio` |
| `requirement.created` | 需求入库 | `id/title/module/priority/status/version` |
| `requirement.draft_generated` | 生成需求草稿 | `id/title/priority/confidence/similar_feedback/impact_customers` |
| `requirement.reviewed` | 产品经理审核 | `id/decision/reviewer/status/version` |

端到端：客户端带上 `X-Trace-Id` 调 `feedback.submit` →
`requirement.analyze` → `requirement.generate_draft` → `requirement.review`，
日志中心按该 trace_id 可还原完整业务链（实测：`GET /api/trace/{trace_id}`
返回 8 条按时间排序的日志，覆盖 submit×2 → analyze → create → draft → review）。

**本地日志自循环（防磁盘撑爆）**：`DECP_LOG_FILE_MAX_MB` × `DECP_LOG_FILE_BACKUP`
即磁盘占用上限（默认 50MB × 5 ≈ 250MB）。SDK 路径下达到单文件上限即轮转，
旧日志压缩为 `.gz` 备份（文件数恒 ≤ 1+backup）；SDK 未安装时回落标准库
`RotatingFileHandler`，同样受上限约束（实测：写入 5MB 日志滚动后磁盘占用
~1MB，文件数受控）。

配置映射：`DECP_` 前缀环境变量（config 的 `log_center_*` 字段）→ SDK 的
`LOG_CENTER_*` 环境变量约定（`_sync_env_from_settings`）。已在容器镜像内集成，
`DECP_LOG_CENTER_ENABLE=true` 即从容器上报日志中心（端到端实测通过）。

## 存储配置

默认 SQLite（`data/decp.db`）。切换 PostgreSQL（复制 `.env` 并按需修改）：

```env
DECP_STORAGE_BACKEND=postgres
DECP_PG_HOST=127.0.0.1
DECP_PG_PORT=5432
DECP_PG_DB=decp
DECP_PG_USER=decp
DECP_PG_PASSWORD=******
```

首次运行自动建表（feedback / requirement / app_meta）。

## 运行 MCP server

```bash
# stdio（agent 通过 mcp client 连接）
decp-mcp
# 或 python -m decp_core.mcp_.main

# streamable HTTP（端口 18100）
python -m decp_core.mcp_.main --transport http --port 18100
```

## Docker 部署

```bash
# 构建镜像（多阶段，非 root，约 78MB）
docker build -t decp-core:latest .

# stdio 模式（MCP 客户端注入 stdin/stdout 连接，如 AgentScope MCPClient）
docker run --rm -i -v decp-data:/app/data decp-core:latest

# streamable http 模式（连接地址 http://localhost:18100/mcp）
docker run --rm -d -p 18100:18100 \
  -e DECP_MCP_TRANSPORT=http -v decp-data:/app/data decp-core:latest

# compose：SQLite 单机
docker compose up -d --build decp-mcp

# compose：PostgreSQL 生产形态
DECP_PG_PASSWORD=你的强密码 docker compose --profile postgres up -d --build
```

容器内关键环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DECP_MCP_TRANSPORT` | `stdio` | `stdio`（MCP 客户端注入）\| `http` |
| `DECP_MCP_PORT` | `18100` | http 监听端口 |
| `DECP_STORAGE_BACKEND` | `sqlite` | `sqlite` \| `postgres` |
| `DECP_DATA_DIR` | `/app/data` | 数据根目录（sqlite/reports 默认基准） |
| `DECP_SQLITE_PATH` | `$DECP_DATA_DIR/decp.db` | SQLite 文件 |
| `DECP_REPORTS_DIR` | `$DECP_DATA_DIR/reports` | 报告输出 |
| `DECP_PG_HOST` | `postgres` | compose 服务名 |

- 数据落卷不落镜像层（`decp-data` / `pg-data`），镜像内非 root 运行。
- http 模式自动启用健康检查看门狗（`scripts/docker/healthcheck.py`）。
- 完整部署说明见 [docs/docker-deployment.md](docs/docker-deployment.md)。

容器内使用数字员工验证闭环：

```bash
# 种子数据 + 数字员工演示（同一容器内执行）
docker run --rm -it -v decp-data:/app/data decp-core:latest \
  sh -c "python -m decp_core.cli.seed --count 5 \
         && decp-demo --instruction '收集反馈并分析，生成需求草稿'"
```

## 测试

```bash
# SQLite 后端全套
pytest

# PostgreSQL 后端测试：读取 .env / 环境变量的 DECP_PG_*（.env 已配置则直接通过，无配置自动跳过）
pytest

# 校验 skills/ 目录技能定义及其 MCP 工具依赖
python -c "from decp_core.agent.skill_catalog import SkillCatalog; s=SkillCatalog('skills').scan(); [print(x.name, x.version, len(x.tools)) for x in s]"
```

## 目录结构

```
src/decp_core/
  config/        # 配置（环境变量 + .env，前缀 DECP_）
  models/        # 领域模型：Feedback / Requirement / AnalysisResult / SourceRef
  storage/       # 存储抽象 + SQLite / PostgreSQL 双后端
  services/      # 企业数据层业务逻辑
  report/        # HTML / Excel 报告导出
  mcp_/          # MCP server：工具注册、入口（stdio / http）
  agent/         # 数字员工：Skill 基类、技能实现、SkillCatalog、注册表、意图路由
  cli/           # seed / demo 命令行
skills/          # 技能定义（SKILL.md + manifest.json），多运行时兼容
scripts/docker/  # 容器入口脚本 + 健康检查
Dockerfile       # 多阶段镜像（非 root）
docker-compose.yml
docs/            # 设计文档（场景 SVG）+ 运行时对接指南 + Docker 部署
tests/           # 存储 / 服务 / MCP 工具 / Skill 层测试
```

## 技能与运行时对接

外部 Agent Runtime（AgentScope、deerflow、Claude Code 等）通过标准 **MCP 协议**调用 DECP 数据能力，技能流程定义在 `skills/*/SKILL.md`：

```
Agent Runtime（成熟系统）
   │ 读取 SKILL.md 理解技能流程（Claude Code / AgentScope 均原生支持）
   ▼
DECP MCP server（stdio / streamable http，13 个工具）
   ▼
DECP 数据域（feedback / requirement / app_meta，SQLite / PostgreSQL）
```

对接要点：
- `skills/` 目录遵循 **Claude Code / AgentScope 同源**的技能规范（SKILL.md frontmatter: `name`/`version`/`description` + 正文知识包），无需转换即可加载。
- AgentScope 接入方式（`LocalSkillLoader` + `MCPClient`，已实测端到端落库）见 [docs/agentscope-integration.md](docs/agentscope-integration.md)。
- 人工审批由 `requirement.review` 保证：数字员工只能产出 Draft，正式入库必须有产品经理审核记录。

## 设计对齐

- **数据主权铁律**：Gateway 遵循数据边界，业务数据始终处于 Workspace 策略控制之下；人类保持最终责任。
- **人工审批不可绕过**：数字员工生成物只能是草稿（Draft），正式入库必须经产品经理 `requirement.review` 批准。
- **来源追踪**：每条需求携带 `source_refs`（可追溯反馈来源）。
- **全流程治理**：身份委托 · 最小权限 · 出站控制 · 人工审批 · 来源追踪 · 版本与 Hash · 全程审计 · Prompt Injection 防护。

> 设计变更优先同步更新 `docs/product-requirement-analysis-scenario_Version2.svg`。
