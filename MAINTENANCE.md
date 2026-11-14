# 本地 fork 维护说明

## 三个远端/目录的角色

| 位置 | 用途 |
|---|---|
| `origin` = `Lewis1David/vertex2openai` | 你的 fork，用来推自己的改动 |
| `upstream` = `bad-woman/vertex2openai` | 原作者仓库，更新的来源 |
| `local` 分支 | 你的补丁所在的分支，永远 rebase 在上游之上 |
| `vertex2openai-orig/`、`vertex2openai-main/` | 旧的目录拷贝，仅作对照，可删 |

`main` 分支保持干净（= 上游镜像），**不要**把自己的改动提交到 main，
这样 `git fetch upstream` + `rebase` 永远不用处理分叉。

## 提交结构

```
local
├─ chore: upstream base snapshot (bad-woman/vertex2openai @ 4c28094)
├─ feat(proxy): outbound proxy pool, console hot-switch
├─ chore: add update.sh and MAINTENANCE.md
└─ chore: update.sh 支持 fork(origin) + upstream 双远端
```

第一个提交的树 = 上游 4c28094 的树，所以“补丁”就是它之后的提交本身，不需要再维护 .patch 文件。

## 日常更新

```bash
bash update.sh              # fetch upstream -> rebase -> docker build -> 打 tag
PUSH=1 bash update.sh       # 额外强推回 fork 的 local 分支
```

首次运行前确认远端（脚本也会自动补）：

```bash
git remote add origin   https://github.com/Lewis1David/vertex2openai.git
git remote add upstream https://github.com/bad-woman/vertex2openai.git
```

## 第一次 rebase 会撞上的地方

你的快照基于上游 `4c28094`，上游现在已经前进 15 个提交（`2ff511c`），其中：

| 上游改动 | 与你补丁的关系 |
|---|---|
| `app/upstreams/cookie_proxy.py` +620/−379 | **必冲突**，上游重写了这个文件，你只插了几行代理 |
| `app/message_processing.py` +33/−8 | 可能冲突（你改了图片下载的代理） |
| `app/main.py` +3/−3 | 大概率自动过（你改的是控制台卡片 144 行） |
| `app/upstreams/express_sdk.py` +44/−10 | 你的补丁没碰它；**但以后的 per-key 补丁要改它**，注意新代码 |

cookie_proxy 冲突的处理原则：**以上游版本为准，把你的 2~3 行代理挂钩重新加回去**，不要保留旧文件内容。

如果冲突太多，更干净的做法是重新起分支：

```bash
git clone https://github.com/Lewis1David/vertex2openai.git v2o-new
cd v2o-new && git checkout -b local
git apply -3 /path/to/v2o-local-changes.patch
git add -A && git commit -m "feat(proxy): outbound proxy pool"
```

## 发布与回滚

```yaml
image: vertex2openai:local-20261114-2ff511c
```

保留上一个 tag，回滚就是把 image 改回去再 `up -d`。**不要用 `latest`**。

## 减少维护量的两件事

1. 模型列表别写死：`MODELS_CONFIG_URL` 指远程 `vertexModels.json`。
2. 新功能尽量放新文件，主文件只留 2~3 行调用。
