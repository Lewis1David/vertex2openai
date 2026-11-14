#!/usr/bin/env bash
# 上游更新 -> rebase -> 重建镜像（可选推送回自己的 fork）
#
#   bash update.sh            只更新 + 构建
#   PUSH=1 bash update.sh     更新 + 构建 + 强推到 fork 的 local 分支
set -euo pipefail
cd "$(dirname "$0")"

UPSTREAM=${UPSTREAM:-https://github.com/bad-woman/vertex2openai.git}
ORIGIN=${ORIGIN:-https://github.com/Lewis1David/vertex2openai.git}
BRANCH=${BRANCH:-main}

git remote get-url upstream >/dev/null 2>&1 || git remote add upstream "$UPSTREAM"
git remote get-url origin   >/dev/null 2>&1 || git remote add origin   "$ORIGIN"

git fetch upstream "$BRANCH"

# 本地补丁的基点：首次是快照根提交，之后记录在上游 commit 上
BASE=$(git config --get v2o.base || true)
[ -n "$BASE" ] || BASE=$(git rev-list --max-parents=0 HEAD)

if ! git rebase --onto "upstream/$BRANCH" "$BASE" HEAD; then
  cat <<'EOF'

❗ rebase 停在冲突处。处理步骤：
  1. 编辑冲突文件
  2. git add <冲突文件>
  3. git rebase --continue
  4. 重新执行 bash update.sh
EOF
  exit 1
fi

git config v2o.base "$(git rev-parse "upstream/$BRANCH")"

TAG="local-$(date +%Y%m%d)-$(git rev-parse --short "upstream/$BRANCH")"
docker build -t "vertex2openai:$TAG" .
echo
echo "✅ 构建完成：vertex2openai:$TAG"

if [ "${PUSH:-0}" = "1" ]; then
  git push --force-with-lease origin HEAD:local
  echo "✅ 已推送到 origin/local"
fi
