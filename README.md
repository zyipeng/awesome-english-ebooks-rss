# awesome-english-ebooks RSS

为 [hehonghui/awesome-english-ebooks](https://github.com/hehonghui/awesome-english-ebooks) 生成带 epub/mobi/pdf **直链**的 RSS 2.0 订阅源。

## 为什么需要这个

GitHub 官方的 commit atom feed（`/commits/master.atom`）只包含 commit 标题，没有每期的下载链接。本仓库通过 GitHub Actions 定时枚举源仓库文件树，生成每期一条、内含三种格式直链的 RSS feed，并部署到 GitHub Pages 供 RSS 阅读器订阅。

## 订阅地址

部署后，在任意 RSS 阅读器（Feedly / Inoreader / NetNewsWire / Reeder 等）中添加：

```
https://<你的GitHub用户名>.github.io/<仓库名>/feed.xml
```

每条更新形如：

- 标题：`经济学人 The Economist te_2026.07.25`
- 正文：列出该期 epub / mobi / pdf 三个直链（点击即下载）
- 同时带 `<enclosure>`，支持在阅读器内直接下载 epub

## 文件结构

```
generate_feed.py            # 生成脚本（仅用 Python 标准库）
requirements.txt            # 无第三方依赖
.github/workflows/build-feed.yml   # 定时构建+部署 workflow
```

## 工作原理

1. GitHub Actions 每天 UTC 06:00 / 18:00 触发（也可手动 `workflow_dispatch`）
2. 脚本调用 GitHub **Git Tree API**（`recursive=1`）一次性拉取源仓库整棵文件树
3. 过滤出 `01_economist` / `02_new_yorker` / `04_atlantic` / `05_wired` 目录下的 `.epub/.mobi/.pdf` 文件
4. 按"期"分组，合并所有杂志，按期号日期降序生成 RSS 2.0 feed
5. 部署到 GitHub Pages

**为何用 Git Tree API 而非 Contents API**：单次调用拿到全部路径（718+ 文件），避免逐目录枚举导致的 API 限流。在 Actions 中用自动注入的 `GITHUB_TOKEN`，限额 5000/小时，绰绰有余。

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `PER_MAGAZINE_LIMIT` | `20` | 每本杂志最多保留最近多少期 |

如需增减杂志，编辑 [generate_feed.py](generate_feed.py) 顶部的 `MAGAZINES` 字典。

## 本地运行

```bash
# 可选：设置 token 提高 API 限额（不设也能跑，但易限流）
export GITHUB_TOKEN=ghp_xxx

python3 generate_feed.py feed.xml
```

## 首次部署步骤

1. 在 GitHub 新建一个空仓库（public，否则 Pages 需 Pro）
2. 把本目录文件推上去
3. 仓库 Settings → Pages → Source 选 **GitHub Actions**
4. 手动触发一次 Actions（Run workflow），完成后即得到订阅 URL
