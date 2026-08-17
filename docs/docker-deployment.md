# DECP Docker 部署指南

> 将 DECP MCP server 容器化，支持 stdio（MCP 客户端注入连接）与 streamable http 两种传输，存储可选用 SQLite（卷挂载）或 PostgreSQL（compose 联用）。

## 1. 镜像构建

```bash
cd /home/decp
docker build -t decp-core:latest .
```

多阶段构建：阶段 1（`build`）用 pip wheel 收集依赖与项目 wheel；阶段 2（`runtime`）仅安装产物，镜像精简、非 root 运行（`decp` 用户）、数据目录卷挂载。

## 2. 运行方式

> ⚠️ **`--rm` 仅用于一次性验证**：容器退出时自动删除容器与数据，不适合正式服务。正式运行请使用下面的命令（`-d` 后台、`--name` 固定容器名、`-v` 数据卷持久化）。

### 2.1 streamable http 模式（正式生产，推荐）

```bash
docker run -d --name decp-mcp \
  -p 18100:18100 \
  -e DECP_MCP_TRANSPORT=http \
  -e DECP_MCP_PORT=18100 \
  -v decp-data:/app/data \
  decp-core:latest
```

连接地址：`http://localhost:18100/mcp`（MCP Streamable HTTP 端点）。

### 2.2 stdio 模式（默认）— MCP 客户端注入

容器以 stdio 传输常驻，由 MCP 客户端（Claude Code、AgentScope 等）注入 stdin/stdout：

```bash
docker run -d --name decp-mcp \
  -v decp-data:/app/data \
  decp-core:latest
```

- `-i` 保持 stdin 打开（MCP 客户端连接所必需）。
- SQLite 数据持久化在 `decp-data` 卷（`/app/data/decp.db`）。

### 2.3 透传命令（容器内执行 seed / demo / 任意命令）

入口脚本支持透传 CMD：不带参数启动 MCP server，带参数时执行该命令：

```bash
# 容器内跑 seed（写入种子反馈）
docker run --rm -i -v decp-data:/app/data decp-core:latest \
  python -m decp_core.cli.seed --count 3

# 容器内跑数字员工演示
docker run --rm -i -v decp-data:/app/data decp-core:latest \
  decp-demo --instruction "收集反馈并分析，生成需求草稿"

# 任意 shell
docker run --rm -it -v decp-data:/app/data decp-core:latest sh
```

> 透传命令是一次性操作，使用 `--rm` 合理；正式常驻服务请用 2.1 / 2.2 的命令。

### 2.4 环境变量

### 2.3 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DECP_MCP_TRANSPORT` | `stdio` | `stdio` \| `http` |
| `DECP_MCP_PORT` | `18100` | http 监听端口 |
| `DECP_STORAGE_BACKEND` | `sqlite` | `sqlite` \| `postgres` |
| `DECP_SQLITE_PATH` | `/app/data/decp.db` | SQLite 文件（容器内） |
| `DECP_REPORTS_DIR` | `/app/data/reports` | 报告输出目录 |
| `DECP_DATA_DIR` | `/app/data` | 数据卷根目录 |
| `DECP_PG_HOST` | `postgres` | PG 主机（compose 服务名） |
| `DECP_PG_PORT` | `5432` | |
| `DECP_PG_DB` | `decp` | |
| `DECP_PG_USER` | `decp` | |
| `DECP_PG_PASSWORD` | `1qaz2wsx` | 生产环境务必通过 secret 注入 |
| `DECP_LOG_LEVEL` | `INFO` | |

## 3. Docker Compose（推荐）

```bash
# SQLite 单机（默认）
docker compose up -d --build decp-mcp

# PostgreSQL 生产形态
DECP_PG_PASSWORD=你的强密码 \
docker compose --profile postgres up -d --build
```

Compose 服务：

| 服务 | 说明 |
| --- | --- |
| `decp-mcp` | MCP server，默认 stdio；`DECP_MCP_TRANSPORT=http` 时暴露 `18100` |
| `postgres` | PostgreSQL 16（profile: `postgres`），healthcheck 就绪后 decp-mcp 才启动 |

> 说明：`postgres` 服务使用 `profiles` 隔离，默认不启动；需要 PG 时加 `--profile postgres`。切换存储到 PG：`DECP_STORAGE_BACKEND=postgres`（compose 已注入 `DECP_PG_HOST=postgres` 指向该服务）。

## 4. 健康检查

`scripts/docker/healthcheck.py` 支持三种模式：

| 模式 | 用途 |
| --- | --- |
| `decp-healthcheck --port 18100` | TCP 端口探测（http 模式，Docker HEALTHCHECK） |
| `decp-healthcheck --stdio` | 进程存活探测（stdio 模式） |
| `decp-healthcheck --port 18100 --watch` | 看门狗：server 崩溃时给 PID 1 发 SIGTERM，容器以失败码退出以触发 restart |

http 模式下 entrypoint 自动后台启动 `--watch` 看门狗，Docker HEALTHCHECK 负责周期探测。

## 5. 验证（冒烟）

```bash
# http 模式：MCP initialize 握手
curl -s -X POST http://localhost:18100/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}'
# 预期返回 result.capabilities（tools/list 等）

# 列出 13 个工具
curl -s -X POST http://localhost:18100/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

```bash
# stdio 模式：容器内 seed + 数字员工冒烟（已实测）
docker run --rm -i -v decp-data:/app/data decp-core:latest \
  sh -c "python -m decp_core.cli.seed --count 3 \
         && decp-demo --instruction '查看最近的反馈'"
# 预期：已写入 3 条反馈 → 路由到 query 技能 → 返回反馈列表
```

## 6. 治理与运维要点

- **数据主权**：业务数据仅存于 `decp-data` / `pg-data` 卷，不进入镜像层；备份即备份卷。
- **非 root**：容器内以 `decp` 用户运行（镜像内降权），卷需可写（Docker 自动处理命名卷属主）。
- **secret**：`DECP_PG_PASSWORD` 等敏感项生产环境用 compose `.env` 或 Docker Secrets，勿硬编码进镜像。
- **来源审计**：日志在容器 stdout（`docker logs decp-mcp`）；数据库自带版本与 Hash 审计（app_meta）。
- **镜像体积**：多阶段构建已移除构建依赖；如需更小可替换 `python:3.12-slim` 为 `python:3.12-alpine`（注意 psycopg binary wheel 兼容性）。

## 7. 相关文件

| 文件 | 说明 |
| --- | --- |
| `Dockerfile` | 多阶段镜像 |
| `.dockerignore` | 排除构建上下文 |
| `docker-compose.yml` | compose 编排（decp-mcp + postgres） |
| `scripts/docker/docker-entrypoint.sh` | 容器入口（传输模式切换 + 看门狗） |
| `scripts/docker/healthcheck.py` | 健康检查脚本 |
