#!/usr/bin/env bash
# sync-npm-skills.sh — 把源 skills/ 同步到 npm/skills/（npm 包携带的独立副本）。
#
# 背景：npm/package.json 的 files 含 "skills/"（npm 包分发技能），但 npm/skills/
# 是 git 跟踪的独立副本。为避免手工同步遗漏（曾导致 npm 包携带旧技能），
# 发布前必须执行本脚本把源 skills/ 的内容同步过去。
#
# 约定：
#   - 只同步 4 个技能目录 + README.md；**不包含 decp-skills.zip**（构建产物）。
#   - 源 skills/ 是唯一事实源；npm/skills/ 是被同步的镜像。
#   - 用 rsync --delete 保证镜像完全一致（源侧删除的文件在镜像侧也删除）。
#
# 用法：
#   ./scripts/sync-npm-skills.sh            # 同步并展示差异摘要
#   ./scripts/sync-npm-skills.sh --check    # 只校验两侧一致，不同步（CI 用）
#
# 退出码：--check 模式下两侧不一致返回 1；同步成功返回 0。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_DIR="$ROOT_DIR/skills"
NPM_DIR="$ROOT_DIR/npm/skills"

# 需要同步的条目（与 package-skills.sh INCLUDE_DIRS 对齐 + soul + README，天然排除 zip）
SYNC_ITEMS=("README.md" "feedback-collect" "requirement-analysis" "requirement-query" "soul")

check_in_sync() {
    local missing=0
    for item in "${SYNC_ITEMS[@]}"; do
        if [ ! -e "$NPM_DIR/$item" ]; then
            echo "❌ npm/skills 缺少 $item（源存在）"
            missing=1
        fi
    done
    if [ -e "$NPM_DIR/decp-skills.zip" ]; then
        echo "❌ npm/skills 不应包含 decp-skills.zip（构建产物，请删除）"
        missing=1
    fi
    # 逐目录比对（跳过 zip 与 npm 特有的文件）
    if ! diff -rq "$SRC_DIR" "$NPM_DIR" \
            -x decp-skills.zip \
            -x '.DS_Store' >/dev/null 2>&1; then
        echo "❌ npm/skills 与源 skills/ 存在差异（--check 不自动修复，请运行同步）"
        diff -rq "$SRC_DIR" "$NPM_DIR" -x decp-skills.zip -x '.DS_Store' | head -20
        missing=1
    fi
    if [ "$missing" = 0 ]; then
        echo "✅ npm/skills 与源 skills/ 一致（4 技能 + README，无 zip）"
        return 0
    fi
    return 1
}

sync() {
    echo ">>> 同步 npm/skills ← skills/ ..."
    mkdir -p "$NPM_DIR"
    for item in "${SYNC_ITEMS[@]}"; do
        if [ -e "$SRC_DIR/$item" ]; then
            # 先清空目标再复制，避免 cp -r 把源目录嵌套进已存在的目标目录
            rm -rf "$NPM_DIR/$item"
            cp -r "$SRC_DIR/$item" "$NPM_DIR/$item"
        fi
    done
    # 清理镜像侧不应存在的条目（zip / 残留）
    for extra in "$NPM_DIR"/decp-skills.zip "$NPM_DIR"/soul.md; do
        [ -e "$extra" ] && rm -f "$extra" && echo "  清理残留: ${extra#$NPM_DIR/}"
    done
    # 删除源侧已移除的技能目录
    for dir in "$NPM_DIR"/*/; do
        [ -d "$dir" ] || continue
        base="$(basename "$dir")"
        if [ ! -e "$SRC_DIR/$base" ]; then
            rm -rf "$dir"
            echo "  删除已移除: $base"
        fi
    done
    echo ""
    if check_in_sync; then
        echo "    已同步: npm/skills 与源 skills/ 一致"
    else
        echo "ERROR: 同步后校验失败"
        exit 1
    fi
}

case "${1:-}" in
    --check) check_in_sync ;;
    *)       sync ;;
esac
