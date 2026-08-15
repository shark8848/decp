#!/usr/bin/env bash
# publish-npm.sh — Build and upload decp-core npm package.
#
# Usage:
#   ./scripts/publish-npm.sh                          # Full build + publish
#   ./scripts/publish-npm.sh --dry-run                # npm pack only, no upload
#   ./scripts/publish-npm.sh --no-skip-existing       # Fail if version exists
#   ./scripts/publish-npm.sh --test                   # Publish with test tag
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
NPM_DIR="$ROOT_DIR/npm"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DRY_RUN=false
SKIP_EXISTING=true
TEST_TAG=false

for arg in "$@"; do
    case "$arg" in
        --dry-run)          DRY_RUN=true ;;
        --no-skip-existing) SKIP_EXISTING=false ;;
        --test)             TEST_TAG=true ;;
        -h|--help)
            echo "Usage: $0 [--dry-run] [--no-skip-existing] [--test]"
            echo ""
            echo "  --dry-run           npm pack only, no publish"
            echo "  --no-skip-existing  Fail if version already exists on npm"
            echo "  --test              Publish with tag 'test' (beta-like)"
            echo ""
            echo "Prerequisites: npm login (npm whoami must succeed)"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Check npm auth
# ---------------------------------------------------------------------------
if ! npm whoami >/dev/null 2>&1; then
    echo "ERROR: npm 未登录。请先执行 npm login。"
    exit 1
fi
NPM_USER="$(npm whoami 2>/dev/null)"
echo "登录用户: $NPM_USER"

# ---------------------------------------------------------------------------
# Build (npm pack) + test
# ---------------------------------------------------------------------------
echo "========================================"
echo " @shark8848/decp-core npm publish"
echo " 目录:    $NPM_DIR"
echo " 用户:    $NPM_USER"
echo "========================================"

cd "$NPM_DIR"

echo ">>> 运行冒烟测试（npm test）..."
DECP_VENV_DIR="${DECP_VENV_DIR:-}" npm test

echo ""
echo ">>> 打包（npm pack --dry-run 确认内容）..."
npm pack --dry-run

echo ""
echo ">>> 生成 tarball..."
TGZ="$(npm pack --silent)"
echo "    产物: $TGZ"

if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "✅ 构建完成（--dry-run，未发布）: $TGZ"
    echo "   发布: ./scripts/publish-npm.sh"
    exit 0
fi

# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------
echo ""
echo ">>> 发布到 npm registry..."

PUBLISH_ARGS=("--access" "public")
if [ "$TEST_TAG" = true ]; then
    PUBLISH_ARGS+=(--tag test)
fi

# --skip-existing 不是 npm publish 的标准 flag，先确认版本是否已存在
VERSION="$(node -p "require('./package.json').version")"
if npm view @shark8848/decp-core@"$VERSION" version >/dev/null 2>&1; then
    if [ "$SKIP_EXISTING" = true ]; then
        echo "版本 $VERSION 已存在于 npm，跳过（--skip-existing）。"
        exit 0
    else
        echo "ERROR: 版本 $VERSION 已存在（--no-skip-existing）。"
        exit 1
    fi
fi

npm publish "${PUBLISH_ARGS[@]}"

echo ""
echo "✅ Published @shark8848/decp-core $VERSION to npm (user: $NPM_USER)"
