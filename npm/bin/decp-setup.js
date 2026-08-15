#!/usr/bin/env node
/**
 * decp-setup.js — 一键准备 DECP MCP server 运行环境。
 *
 * 流程：
 *   1. 检测 python3 可用性（版本 >= 3.12）
 *   2. 在 ~/.decp/venv 创建（或复用）虚拟环境
 *   3. pip install --upgrade decp-core（PyPI 拉取）
 *   4. 验证 decp_core 可导入
 *
 * 用法：
 *   node bin/decp-setup.js            # 完整安装
 *   node bin/decp-setup.js --check    # 仅校验环境是否就绪
 *   node bin/decp-setup.js --venv PATH  # 自定义 venv 路径
 */
'use strict';

const { spawnSync, spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const MIN_PYTHON = [3, 12];
const DEFAULT_VENV_DIR = path.join(os.homedir(), '.decp', 'venv');
const EXTRA = process.env.DECP_SETUP_EXTRA || '';

function log(msg) {
  console.error(`[decp-setup] ${msg}`);
}

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
  return r.status === 0;
}

function findPython() {
  const candidates = ['python3', 'python'];
  for (const c of candidates) {
    const r = spawnSync(c, ['--version'], { encoding: 'utf8' });
    if (r.status === 0 && r.stdout) {
      const m = r.stdout.match(/Python (\d+)\.(\d+)/);
      if (m) {
        const major = +m[1], minor = +m[2];
        if (major > MIN_PYTHON[0] || (major === MIN_PYTHON[0] && minor >= MIN_PYTHON[1])) {
          return c;
        }
        log(`发现 ${c} 版本 ${major}.${minor}，需要 >= ${MIN_PYTHON.join('.')}`);
      }
    }
  }
  return null;
}

function venvPython(venvDir) {
  return path.join(venvDir, 'bin', 'python');
}

function isReady(venvDir) {
  const py = venvPython(venvDir);
  if (!fs.existsSync(py)) return false;
  const r = spawnSync(py, ['-c', 'import decp_core; print("ok")'], { encoding: 'utf8' });
  return r.status === 0 && r.stdout.includes('ok');
}

function main() {
  const args = process.argv.slice(2);
  const checkOnly = args.includes('--check');
  const venvIdx = args.indexOf('--venv');
  const venvDir = venvIdx >= 0 ? path.resolve(args[venvIdx + 1]) : DEFAULT_VENV_DIR;

  if (checkOnly) {
    if (isReady(venvDir)) {
      log(`环境就绪: ${venvDir}`);
      process.exit(0);
    }
    log(`环境未就绪: ${venvDir}（运行 decp-setup 安装）`);
    process.exit(1);
  }

  log(`venv 目录: ${venvDir}`);
  if (isReady(venvDir)) {
    log('decp-core 已安装，跳过。');
    return;
  }

  const py = findPython();
  if (!py) {
    log(`错误: 未找到 python3 >= ${MIN_PYTHON.join('.')}，请先安装 Python。`);
    process.exit(1);
  }
  log(`使用 ${py}`);

  if (!fs.existsSync(path.join(venvDir, 'bin', 'python'))) {
    log('创建虚拟环境…');
    if (!run(py, ['-m', 'venv', venvDir])) {
      log('创建 venv 失败');
      process.exit(1);
    }
  }

  const vpy = venvPython(venvDir);
  const pipArgs = ['-m', 'pip', 'install', '--upgrade'];
  if (EXTRA) pipArgs.push(EXTRA);
  pipArgs.push('decp-core');
  log('安装 decp-core（PyPI）…');
  if (!run(vpy, pipArgs)) {
    log('pip install 失败');
    process.exit(1);
  }

  if (isReady(venvDir)) {
    log('✅ 安装完成。运行 decp-mcp 启动 MCP server。');
  } else {
    log('安装完成但验证失败，请手动检查。');
    process.exit(1);
  }
}

main();
