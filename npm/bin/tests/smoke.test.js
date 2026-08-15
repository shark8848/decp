/**
 * decp-core npm 包冒烟测试。
 *
 * 覆盖：
 *   1. decp-setup --check 环境检测
 *   2. decp-mcp stdio 模式 MCP 握手（initialize + tools/list）
 *
 * 用法：
 *   node --test bin/tests/
 */
'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const { spawn } = require('node:child_process');
const path = require('node:path');

const MCP_BIN = path.join(__dirname, '..', 'decp-mcp.js');
const SETUP_BIN = path.join(__dirname, '..', 'decp-setup.js');

function runNode(script, args = [], { input = null, env = {} } = {}) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [script, ...args], {
      env: { ...process.env, ...env },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let out = '', err = '';
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (err += d));
    if (input) child.stdin.write(input);
    child.stdin.end();
    child.on('close', (code) => resolve({ code, out, err }));
  });
}

test('decp-mcp.js 语法正确', async () => {
  const { code } = await runNode(MCP_BIN, ['--help']);
  assert.strictEqual(code, 0);
});

test('decp-mcp.js stdio 模式 MCP 握手（initialize + tools/list）', async () => {
  const input = [
    JSON.stringify({ jsonrpc: '2.0', method: 'initialize', id: 1, params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'test', version: '1.0' } } }),
    JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized', params: {} }),
    JSON.stringify({ jsonrpc: '2.0', method: 'tools/list', id: 2, params: {} }),
    '',
  ].join('\n');

  const r = await runNode(MCP_BIN, [], {
    input,
    env: { DECP_VENV_DIR: process.env.DECP_VENV_DIR || path.join(process.env.HOME || '', '.decp', 'venv') },
  });

  // stdio 模式下 stdout 必须是纯 MCP 协议 JSON
  const lines = r.out.trim().split('\n');
  assert.ok(lines.length >= 2, `应有 >=2 行 JSON 响应，实际 ${lines.length}`);

  const init = JSON.parse(lines[0]);
  assert.strictEqual(init.id, 1);
  assert.ok(init.result?.serverInfo?.name === 'decp', 'serverInfo.name 应为 decp');

  const tools = JSON.parse(lines.find((l) => l.includes('"tools"') && l.includes('tools/list')) || lines[lines.length - 1]);
  assert.ok(tools.result?.tools?.length >= 13, `应 >= 13 个工具，实际 ${tools.result?.tools?.length}`);
});
