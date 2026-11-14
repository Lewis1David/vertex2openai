#!/usr/bin/env bash
# 上游更新 -> rebase -> 重建镜像
# 用法：bash update.sh          （在服务器上跑，需要有 docker 和网络）
set -euo pipefail
cd "$(dirname "$0")"

REMOTE=${REMOTE:-https://github.com/bad-woman/vertex2openai.git}
BRANCH=${BRANCH:-main}

# 第一次运行时补上远端
git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REMOTE"
git fetch origin "$BRANCH"

# 本地补丁的基点：首次是快照根提交，之后记录在上游 commit 上
BASE=$(git config --get v2o.base || true)
[ -n "$BASE" ] || BASE=$(git rev-list --max-parents=0 HEAD)

if ! git rebase --onto "origin/$BRANCH" "$BASE" HEAD; then
  cat <<'EOF'

❗ rebase 停在冲突处。处理步骤：
  1. 编辑冲突文件（通常是 app/main.py）
  2. git add <文件>
  3. git rebase --continue
  4. 重新执行 bash update.sh
EOF
  exit 1
fi

git config v2o.base "$(git rev-parse "origin/$BRANCH")"

TAG="local-$(date +%Y%m%d)-$(git rev-parse --short "origin/$BRANCH")"
docker build -t "vertex2openai:$TAG" .
echo
echo "✅ 构建完成：vertex2openai:$TAG"
echo "   把 compose 里的 image 改成它，再 docker compose up -d"
