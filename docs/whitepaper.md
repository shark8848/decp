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

### 设计文档

完整场景泳道图（业务人员 / 数字员工 / 数据访问 / 企业数据 四泳道，七步业务闭环）：

![产品需求收集、整理与分析场景](product-requirement-analysis-scenario_Version2.svg)

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

## 7. 许可

MIT License · © 2026 shark8848 &lt;admin@sharky-ai.com&gt;
