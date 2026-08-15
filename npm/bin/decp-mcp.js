#!/usr/bin/env node
/**
 * decp-mcp.js — 启动 DECP MCP server。
 *
 * Node 包装 Python MCP server：
 *   - stdio（默认）：MCP 客户端注入 stdin/stdout，Node 作为 Python 子进程的
 *     转发层（stdio passthrough）
 *   - http：--transport http --port 18100
 *
 * 环境变量（透传给 Python）：
 *   DECP_MCP_TRANSPORT   stdio | http
 *   DECP_MCP_PORT        18100
 *   DECP_STORAGE_BACKEND sqlite | postgres
 *   ...（完整见 pyproject.toml / README）
 *
 * 用法：
 *   npx decp-mcp                     # stdio
 *   npx decp-mcp --transport http    # http，端口 18100
 *   npx decp-mcp --transport http --port 18100
 */
'use strict';

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const VENV_DIR = process.env.DECP_VENV_DIR || path.join(os.homedir(), '.decp', 'venv');
const VENV_PYTHON = path.join(VENV_DIR, 'bin', 'python');
const MODULE = 'decp_core.mcp_.main';

function log(msg) {
  console.error(`[decp-mcp] ${msg}`);
}

function ensureVenv() {
  if (!fs.existsSync(VENV_PYTHON)) {
    log(`未找到 venv (${VENV_PYTHON})`);
    log('首次使用请先运行: npx decp-setup');
    process.exit(1);
  }
}

function parseArgs(argv) {
  const args = { transport: 'stdio', port: '18100', passthrough: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--transport') args.transport = argv[++i] || 'stdio';
    else if (a === '--port') args.port = argv[++i] || '18100';
    else if (a === '--help' || a === '-h') {
      console.log(`
decp-mcp — DECP MCP server launcher

用法:
  npx decp-mcp                          stdio（MCP 客户端注入 stdin/stdout）
  npx decp-mcp --transport http         streamable http（默认端口 18100）
  npx decp-mcp --transport http --port 18100

环境变量:
  DECP_VENV_DIR          自定义 venv 路径（默认 ~/.decp/venv）
  DECP_MCP_TRANSPORT     stdio | http
  DECP_MCP_PORT          http 端口
  DECP_STORAGE_BACKEND   sqlite | postgres
  DECP_PG_HOST/DB/USER/PASSWORD   PostgreSQL 连接
`);
      process.exit(0);
    } else {
      args.passthrough.push(a);
    }
  }
  return args;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  ensureVenv();

  const pyArgs = ['-m', MODULE];
  if (args.transport === 'http') {
    pyArgs.push('--transport', 'http', '--port', args.port);
  }
  pyArgs.push(...args.passthrough);

  log(`启动: ${args.transport} 模式 (${VENV_PYTHON} -m ${MODULE})`);
  const child = spawn(VENV_PYTHON, pyArgs, {
    stdio: 'inherit', // stdin/stdout 透传 → MCP 协议；stderr → 日志
    env: {
      ...process.env,
      DECP_MCP_TRANSPORT: args.transport,
      DECP_MCP_PORT: args.port,
    },
  });

  child.on('exit', (code, signal) => {
    if (signal) log(`MCP server 被信号 ${signal} 终止`);
    process.exit(code ?? 0);
  });
  child.on('error', (err) => {
    log(`启动失败: ${err.message}`);
    process.exit(1);
  });
}

main();
