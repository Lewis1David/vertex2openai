#!/usr/bin/env bash
# 上游更新 -> rebase -> 重建镜像（可选推送回自己的 fork）
#
#   bash update.sh            更新 + 构建（没装 docker 就跳过构建）
#   PUSH=1 bash update.sh     额外强推到 fork 的 local 分支
set -euo pipefail
cd "$(dirname "$0")"

UPSTREAM=${UPSTREAM:-https://github.com/bad-woman/vertex2openai.git}
ORIGIN=${ORIGIN:-https://github.com/Lewis1David/vertex2openai.git}
BRANCH=${BRANCH:-main}
TARGET="upstream/$BRANCH"

# 0. 别在未完成的 rebase 上再套一层
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
  echo "❗ 当前有未完成的 rebase，先处理：git rebase --continue 或 git rebase --abort"
  exit 1
fi

# 1. 远端
git remote get-url upstream >/dev/null 2>&1 || git remote add upstream "$UPSTREAM"
git remote get-url origin   >/dev/null 2>&1 || git remote add origin   "$ORIGIN"

# 2. 提交身份（bundle clone 不会带仓库级配置，缺了 rebase 会直接停）
if [ -z "$(git config --get user.name || true)" ] || [ -z "$(git config --get user.email || true)" ]; then
  git config user.name  "$(git log -1 --format=%an)"
  git config user.email "$(git log -1 --format=%ae)"
  echo "ℹ️ 未配置提交身份，已按最近一次提交的作者设置：$(git config user.name) <$(git config user.email)>"
fi

git fetch upstream "$BRANCH"

# 3. 已经含上游最新提交就不用动
if git merge-base --is-ancestor "$TARGET" HEAD; then
  echo "✅ 已在 $TARGET ($(git rev-parse --short "$TARGET")) 之上，跳过 rebase"
else
  # 基点：优先用记录值；否则取共同祖先；都没有才用根提交（快照仓库）
  BASE=$(git config --get v2o.base || true)
  if [ -z "$BASE" ]; then
    BASE=$(git merge-base HEAD "$TARGET" 2>/dev/null || git rev-list --max-parents=0 HEAD)
  fi
  COUNT=$(git rev-list --count "$BASE"..HEAD)
  if [ "$COUNT" -gt 50 ]; then
    echo "❗ 要重放 $COUNT 个提交，基点 $BASE 明显不对，已中止。"
    echo "   修正：git config v2o.base $(git rev-parse --short "$TARGET") 前先确认分支内容"
    exit 1
  fi
  echo "🔁 重放 $COUNT 个本地提交到 $TARGET ($(git rev-parse --short "$TARGET"))"
  if ! git rebase --onto "$TARGET" "$BASE" HEAD; then
    cat <<'EOF'

❗ rebase 停在冲突处：
  1. 编辑冲突文件（常见 app/upstreams/cookie_proxy.py、app/message_processing.py）
  2. git add <冲突文件>
  3. git rebase --continue
  4. 重新执行 bash update.sh
EOF
    exit 1
  fi
fi

git config v2o.base "$(git rev-parse "$TARGET")"

# 4. 构建（本机没 docker 就跳过，服务器上再跑一次即可）
if command -v docker >/dev/null 2>&1; then
  TAG="local-$(date +%Y%m%d)-$(git rev-parse --short "$TARGET")"
  docker build -t "vertex2openai:$TAG" .
  echo "✅ 构建完成：vertex2openai:$TAG"
else
  echo "⚠️ 没找到 docker，跳过构建"
fi

# 5. 可选：推回 fork
if [ "${PUSH:-0}" = "1" ]; then
  git push --force-with-lease origin HEAD:local
  echo "✅ 已推送到 origin/local"
fi
