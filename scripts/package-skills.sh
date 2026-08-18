#!/usr/bin/env bash
# package-skills.sh — 打包 decp-skills.zip（供异地 Agent Runtime 上传导入）。
#
# 约定：
#   - 只打包 3 个流程技能（feedback-collect / requirement-analysis / requirement-query）
#     + README.md；**排除 soul/ 技能目录与根目录 soul.md**（人格定义不发外部平台）。
#   - 本地 skills/ 目录保留完整 4 技能（含 soul，供 SkillCatalog / Claude Code 加载），
#     zip 是分发裁剪产物。
#
# 用法：
#   ./scripts/package-skills.sh          # 重新生成 skills/decp-skills.zip
#   ./scripts/package-skills.sh --check  # 只校验 zip 不含 soul，不重新打包
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILLS_DIR="$ROOT_DIR/skills"
ZIP_PATH="$SKILLS_DIR/decp-skills.zip"

# 打入 zip 的技能目录（显式枚举，天然排除 soul）
INCLUDE_DIRS=("feedback-collect" "requirement-analysis" "requirement-query"
              "task-management" "bug-management" "meeting-minutes")

check_excludes_soul() {
    if ! unzip -l "$ZIP_PATH" >/dev/null 2>&1; then
        echo "ERROR: 不是有效 zip: $ZIP_PATH"
        exit 1
    fi
    local bad
    bad="$(unzip -l "$ZIP_PATH" | grep -i "soul" || true)"
    if [ -n "$bad" ]; then
        echo "❌ $ZIP_PATH 包含 soul 相关内容（不应打包）："
        echo "$bad"
        exit 1
    fi
    echo "✅ $ZIP_PATH 不含 soul（6 个流程技能 + README）"
}

build() {
    echo ">>> 打包 decp-skills.zip（排除 soul/ 与 soul.md）..."
    rm -f "$ZIP_PATH"
    # 进入 skills 目录打包，zip 内路径为相对路径
    (cd "$SKILLS_DIR" && zip -r decp-skills.zip README.md "${INCLUDE_DIRS[@]}")
    echo ""
    check_excludes_soul
    echo "    产物: $ZIP_PATH"
}

if [ "${1:-}" = "--check" ]; then
    check_excludes_soul
else
    build
fi
