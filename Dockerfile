# syntax=docker/dockerfile:1
# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT

# =============================================================================
# DECP · 联邦数字员工协作平台 — 容器镜像
#
# 运行目标：DECP MCP server（stdio / streamable http），提供 13 个数据工具。
# 存储：SQLite（默认，卷挂载 data/）或 PostgreSQL（compose 联用）。
#
# 构建:
#   docker build -t decp-core:latest .
#
# 运行:
#   # stdio（MCP 客户端注入 stdin/stdout，如 Claude Code / AgentScope）
#   docker run --rm -i -v decp-data:/app/data decp-core:latest
#
#   # streamable http（默认 18100）
#   docker run --rm -p 18100:18100 -e DECP_MCP_TRANSPORT=http decp-core:latest
#
# 健康检查（http 模式）: GET /health（由 entrypoint 自动启用）
# 详细部署说明: docs/docker-deployment.md
# =============================================================================

# ---- 阶段 1：构建依赖（pip wheel，仅收集产物） -----------------------------
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY pyproject.toml ./
# 复制源码（pip wheel 构建项目 wheel 需要源码；.dockerignore 已排除无关内容）
COPY src/ src/
# 项目无 setup.py/setup.cfg，纯 pyproject 构建
# ikc-log-center（远程日志 SDK，pip 可安装）作为常规依赖一并收进 wheel 目录
RUN pip wheel --no-deps --wheel-dir /build/wheels . \
    && pip wheel --wheel-dir /build/wheels \
       "mcp>=2.0,<3.0" "pydantic>=2.6,<3.0" "pydantic-settings>=2.2" \
       "jinja2>=3.1" "openpyxl>=3.1" "psycopg[binary]>=3.1" "psycopg-pool>=3.1" \
       "sqlalchemy>=2.0" "aiosqlite>=0.20" \
       "ikc-log-center>=1.4.10"

# ---- 阶段 2：运行镜像 ------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DECP_DATA_DIR=/app/data \
    DECP_MCP_TRANSPORT=stdio

WORKDIR /app

# 非 root 运行（企业数据主权：容器内降权）
RUN groupadd --system decp && useradd --system --gid decp --home /app decp \
    && mkdir -p /app/data \
    && chown -R decp:decp /app

# 复制已构建 wheel 并安装（不保留构建产物，保持镜像精简）
# ikc-log-center 是可选依赖（logging extra），此处显式安装以启用远程日志上报
COPY --from=build /build/wheels /wheels
RUN pip install --no-index --find-links /wheels decp-core \
    && pip install --no-index --find-links /wheels "ikc-log-center>=1.4.10" \
    && rm -rf /wheels

# 复制技能定义（SKILL.md + manifest.json，供 SkillCatalog / 外部运行时加载）
COPY skills/ /app/skills/

# 容器入口：根据 DECP_MCP_TRANSPORT 切换 stdio / http；http 模式启用健康检查
COPY scripts/docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint
COPY scripts/docker/healthcheck.py /usr/local/bin/decp-healthcheck
RUN chmod +x /usr/local/bin/docker-entrypoint /usr/local/bin/decp-healthcheck

# 数据卷：SQLite 文件与报告输出
VOLUME ["/app/data"]

USER decp

EXPOSE 18100
ENTRYPOINT ["docker-entrypoint"]
