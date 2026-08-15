# syntax=docker/dockerfile:1
# Copyright (c) 2026 shark8848 <admin@sharky-ai.com>
# SPDX-License-Identifier: MIT

# =============================================================================
# DECP · 联邦数字员工协作平台 — 容器镜像（PyPI 安装版）
#
# 本 Dockerfile 面向 npm 包分发：decp-core 从 PyPI 安装，skills 从 npm 包复制。
# 运行目标：DECP MCP server（stdio / streamable http），提供 13 个数据工具。
#
# 构建（在 npm/ 目录内）:
#   docker build -t decp-core:latest -f docker/Dockerfile .
#
# 运行:
#   # stdio（MCP 客户端注入 stdin/stdout，如 Claude Code / AgentScope）
#   docker run --rm -i -v decp-data:/app/data decp-core:latest
#
#   # streamable http（默认 18100）
#   docker run --rm -p 18100:18100 -e DECP_MCP_TRANSPORT=http decp-core:latest
# =============================================================================

FROM python:3.12-slim

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

# 从 PyPI 安装 decp-core + 可选远程日志 SDK
RUN pip install "decp-core>=0.1.0" "ikc-log-center>=1.4.10" \
    && rm -rf /root/.cache/pip

# 复制技能定义（SKILL.md + manifest.json，供 SkillCatalog / 外部运行时加载）
COPY skills/ /app/skills/

# 容器入口：根据 DECP_MCP_TRANSPORT 切换 stdio / http；http 模式启用健康检查
COPY bin/decp-docker-entrypoint.sh /usr/local/bin/docker-entrypoint
COPY bin/decp-healthcheck.py /usr/local/bin/decp-healthcheck
RUN chmod +x /usr/local/bin/docker-entrypoint /usr/local/bin/decp-healthcheck

# 数据卷：SQLite 文件与报告输出
VOLUME ["/app/data"]

USER decp

EXPOSE 18100
ENTRYPOINT ["docker-entrypoint"]
