# DECP Core · npm 包

DECP（联邦数字员工协作平台）的 **skill 定义 + MCP server 启动器**。

通过 `npm install @shark8848/decp-core` 即可获得：

- `skills/` — 3 个技能定义（SKILL.md + manifest.json），可直接被 **Claude Code / AgentScope / deerflow** 加载
- `decp-mcp` — 一行启动 DECP MCP server（Node 包装 Python，自动准备 venv）
- `decp-setup` — 一键安装 Python 运行环境
- `Dockerfile` + `docker-compose.yml` — 容器化部署

## 安装

```bash
npm install @shark8848/decp-core
```

## 快速开始

### 1. 准备 Python 环境（首次）

```bash
npx decp-setup
```

自动完成：检测 python3 ≥ 3.12 → 创建 `~/.decp/venv` → 从 PyPI 安装 `decp-core`。

### 2. 启动 MCP server

```bash
# stdio 模式（默认，供 Claude Code / AgentScope 等 MCP 客户端连接）
npx decp-mcp

# streamable HTTP 模式（端口 18100）
npx decp-mcp --transport http --port 18100
```

MCP server 提供 **13 个工具**：`feedback.*`（提交/查询）、`requirement.*`（分析/草稿/审核/入库）、`report.*`（报告）、`domain.stats`。

### 3. 加载技能

技能定义位于 `node_modules/@shark8848/decp-core/skills/`，复制到你的 Agent Runtime：

**Claude Code：**
```bash
cp -r node_modules/@shark8848/decp-core/skills/* ~/.claude/skills/
```

**AgentScope / deerflow：** 使用各自的 SkillLoader 指向该目录（详见 [docs/agentscope-integration.md](https://github.com/shark8848/decp/blob/main/docs/agentscope-integration.md)）。

技能通过 MCP 协议调用 DECP 数据能力，需先启动 MCP server（步骤 2）。

## Docker 部署

```bash
cd node_modules/@shark8848/decp-core
docker compose up -d --build decp-mcp        # SQLite 单机
DECP_PG_PASSWORD=你的强密码 docker compose --profile postgres up -d --build   # PostgreSQL 生产形态
```

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DECP_MCP_TRANSPORT` | `stdio` | `stdio` \| `http` |
| `DECP_MCP_PORT` | `18100` | http 监听端口 |
| `DECP_STORAGE_BACKEND` | `sqlite` | `sqlite` \| `postgres` |
| `DECP_SQLITE_PATH` | `{data}/decp.db` | SQLite 文件 |
| `DECP_PG_HOST` / `PORT` / `DB` / `USER` / `PASSWORD` | — | PostgreSQL 连接 |
| `DECP_VENV_DIR` | `~/.decp/venv` | decp-setup 创建的 venv 路径 |
| `DECP_SETUP_EXTRA` | — | 额外 pip 安装参数（如 `"ikc-log-center"`） |

完整配置见 [pyproject.toml](https://github.com/shark8848/decp/blob/main/pyproject.toml)。

## 许可证

MIT © 2026 shark8848 <admin@sharky-ai.com>
