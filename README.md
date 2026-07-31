# awesome-english-ebooks RSS

为 [hehonghui/awesome-english-ebooks](https://github.com/hehonghui/awesome-english-ebooks) 生成按杂志分流的 RSS 订阅源。

每本杂志一个独立 RSS，feed 内每篇文章是一条 item，点开标题直接阅读正文（从 epub 提取），无需下载文件。

## 订阅地址

每本杂志一个订阅，按需添加到 RSS 阅读器（Feedly / Inoreader / NetNewsWire / Reeder 等）：

| 杂志 | 订阅 URL |
|---|---|
| 经济学人 | `https://zyipeng.github.io/awesome-english-ebooks-rss/feeds/economist.xml` |
| 纽约客 | `https://zyipeng.github.io/awesome-english-ebooks-rss/feeds/new_yorker.xml` |
| 大西洋月刊 | `https://zyipeng.github.io/awesome-english-ebooks-rss/feeds/atlantic.xml` |
| 连线 | `https://zyipeng.github.io/awesome-english-ebooks-rss/feeds/wired.xml` |

阅读器里的结构：
```
经济学人 文章更新
  ├─ When a president stops pretending that voters count, democracy...
  │   └─ 点开 → 文章正文
  ├─ Donald Trump's Saudi deal risks nuclear proliferation
  │   └─ 点开 → 文章正文
  └─ ...（每篇文章一条）
```

## 文件结构

```
generate_feed.py            # 生成脚本（仅用 Python 标准库）
requirements.txt            # 无第三方依赖
.github/workflows/build-feed.yml   # 定时构建+部署 workflow
```

## 工作原理

1. GitHub Actions 每天 UTC 06:00 / 18:00 触发
2. 脚本调用 Git Tree API 拉取源仓库整棵文件树，定位各杂志每期 epub
3. 对每期 epub：下载 → 解压 → 按 spine 顺序识别单篇文章（跳过广告/封面/目录页）→ 清洗正文 HTML
4. 每篇文章生成一条 RSS item，按杂志归档输出独立 feed 到 `feeds/<magazine>.xml`
5. 部署到 GitHub Pages

文章识别规则：spine 项中有 `<h1>` 标题且纯文字 >300 字的视为文章，其余视为目录/分隔页跳过。

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `PER_MAGAZINE_LIMIT` | `2` | 每本杂志保留最近多少期 |
| `ARTICLE_MAX_CHARS` | `15000` | 单篇文章正文截断到多少字符 |

编辑 [generate_feed.py](generate_feed.py) 顶部的 `MAGAZINES` 字典可增减杂志。

## 本地运行

```bash
export GITHUB_TOKEN=ghp_xxx   # 可选，提高 API 限额
python3 generate_feed.py feeds
```

## 首次部署

1. 在 GitHub 新建一个 public 空仓库
2. 推送本目录文件
3. 仓库 Settings → Pages → Source 选 **GitHub Actions**
4. 手动触发一次 Actions，完成后得到上面四个订阅 URL
