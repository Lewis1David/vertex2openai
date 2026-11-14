# 本地 fork 维护说明

## 目录角色

| 目录 | 用途 |
|---|---|
| `vertex2openai/` | **主仓库**（git，分支 `local`），以后只改这里 |
| `vertex2openai-orig/` | 上游原始快照，只作对照，可删 |
| `vertex2openai-main/` | 旧的手工改法产物，已归档，可删 |

## 提交结构

```
local
├─ chore: upstream base snapshot (bad-woman/vertex2openai @ 4c28094)
└─ feat(proxy): outbound proxy pool, console hot-switch
```

第一个提交的树 = 上游 4c28094 的树，所以“补丁”就是第二个提交本身，不需要再维护 .patch 文件。

## 日常更新（服务器上有 docker + 网络）

```bash
bash update.sh
```

它做三件事：`fetch origin main` → `rebase --onto origin/main` → `docker build` 并打上
`local-日期-上游commit` 的 tag。

第一次运行前，把远端加上（脚本也会自动加）：

```bash
git remote add origin https://github.com/bad-woman/vertex2openai.git
```

## 冲突怎么处理

`rebase` 只在“上游也改过的行”上冲突，按提示 `git add` + `git rebase --continue`。
冲突热点：

| 文件 | 概率 | 说明 |
|---|---|---|
| `app/main.py` | 高 | 控制台单文件 UI，上游每版都在动 |
| `app/config.py` | 中 | 常加新设置项 |
| `app/http_options.py` | 低 | 只加了可选参数 |
| `app/upstreams/express_sdk.py` | 低 | 小改 |
| `app/proxy_manager.py` | 无 | 新增文件 |

想彻底避开 main.py 的冲突，就把控制台那张卡片砍掉，改成纯 env/API 配置。

## 发布与回滚

```yaml
image: vertex2openai:local-20261114-4c28094
```

保留上一个 tag，回滚就是把 image 改回去再 `up -d`。**不要用 `latest`** —— 官方镜像是另一条构建线。

## 减少维护量的两件事

1. 模型列表别写死：`MODELS_CONFIG_URL` 指远程 `vertexModels.json`，上游加模型不用 rebase。
2. 新功能尽量放新文件，主文件只留 2~3 行调用，冲突面最小。
