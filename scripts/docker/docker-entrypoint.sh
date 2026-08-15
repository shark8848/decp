#!/bin/sh
# =============================================================================
# DECP 容器入口：默认启动 MCP server；传入命令时透传执行（如 seed/demo/交互）
#
#   DECP_MCP_TRANSPORT=stdio   (默认)  stdio 传输，MCP 客户端注入 stdin/stdout
#   DECP_MCP_TRANSPORT=http             streamable http，监听 DECP_MCP_PORT
#
# 用法：
#   docker run decp-core:latest                          # 默认启动 MCP server
#   docker run decp-core:latest python -m decp_core.cli.seed   # 透传执行 seed
#   docker run decp-core:latest sh -c "decp-demo --instruction '查看反馈'"
# =============================================================================
set -e

# 容器内数据根目录（sqlite/reports 默认基准），始终初始化
DATA_DIR="${DECP_DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR" "$DATA_DIR/reports"

# 若有 CMD 参数（docker run 后的命令），直接透传执行，不再进入 MCP server 分支
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

TRANSPORT="${DECP_MCP_TRANSPORT:-stdio}"
PORT="${DECP_MCP_PORT:-18100}"

# 启动信息打到 stderr：stdio 模式下 stdout 是 MCP 协议通道，echo 到 stdout 会
# 污染 JSON-RPC 流，导致外部 MCP 客户端解析失败。
echo "[decp] transport=${TRANSPORT} backend=${DECP_STORAGE_BACKEND:-sqlite} data=${DATA_DIR}" >&2

case "$TRANSPORT" in
  stdio)
    # stdio 模式：直接 exec，容器 PID 1 即 server 进程
    exec python -m decp_core.mcp_.main --transport stdio
    ;;
  http)
    # http 模式：后台看门狗（server 崩溃时给 PID 1 发 SIGTERM），随后 exec server
    /usr/local/bin/decp-healthcheck --port "$PORT" --watch &
    exec python -m decp_core.mcp_.main --transport http --port "$PORT"
    ;;
  *)
    echo "[decp] 未知 DECP_MCP_TRANSPORT=${TRANSPORT}（可选: stdio | http）" >&2
    exit 1
    ;;
esac
