#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为 hehonghui/awesome-english-ebooks 生成按杂志分流的 RSS 2.0 订阅源。

每本杂志一个独立 feed（feeds/<magazine>.xml），feed 内每篇文章是一条 item，
正文（description）内嵌从 epub 提取的文章 HTML，点开标题即可阅读。

工作原理：
1. GitHub Git Tree API 拉取整棵文件树，定位各杂志每期 epub
2. 对每期 epub：下载 -> 解压 -> 按 spine 顺序识别单篇文章 -> 清洗正文
3. 每篇文章生成一条 RSS item，按杂志归档输出独立 feed

依赖：仅标准库。GITHUB_TOKEN 可选（提高 API 限额）。
"""

import io
import os
import re
import sys
import json
import zipfile
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

# ---- 配置 ----
REPO = "hehonghui/awesome-english-ebooks"
BRANCH = "master"
# 杂志目录 -> (feed 文件名, 中文名)
MAGAZINES = {
    "01_economist": ("economist", "经济学人 The Economist"),
    "02_new_yorker": ("new_yorker", "纽约客 The New Yorker"),
    "04_atlantic": ("atlantic", "大西洋月刊 The Atlantic"),
    "05_wired": ("wired", "连线 Wired"),
}
# 每本杂志保留最近多少期（每期会下载并解压一本 epub）
PER_MAGAZINE_LIMIT = int(os.environ.get("PER_MAGAZINE_LIMIT", "2"))
# 单篇文章正文截断到多少字符（控制单条体积）
ARTICLE_MAX_CHARS = int(os.environ.get("ARTICLE_MAX_CHARS", "15000"))
# 纯文字少于此字符数的 spine 项视为目录/分隔页，跳过
MIN_ARTICLE_CHARS = 300
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
TREE_API = f"https://api.github.com/repos/{REPO}/git/trees/{BRANCH}?recursive=1"
FEED_LINK = "https://github.com/" + REPO

DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")


def api_get_tree():
    """调用 Git Tree API，返回路径列表。"""
    req = urllib.request.Request(TREE_API)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "awesome-english-ebooks-rss-generator")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("truncated"):
            sys.stderr.write("警告：文件树被截断，feed 可能不完整。\n")
        return [e["path"] for e in data.get("tree", []) if e.get("type") == "blob"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore").lower()
        if e.code == 403 and "rate limit" in body:
            sys.stderr.write("GitHub API 限流，请设置 GITHUB_TOKEN 后重试。\n")
        else:
            sys.stderr.write("API 调用失败 [%s]\n" % e.code)
        return None
    except Exception as e:
        sys.stderr.write("请求异常: %s\n" % e)
        return None


def collect_issues(paths):
    """返回 { mag_dir: { issue_name: (datetime, epub_url) } }，按日期降序。"""
    mag_dirs = set(MAGAZINES.keys())
    issues = defaultdict(dict)
    for path in paths:
        parts = path.split("/")
        if len(parts) < 3:
            continue
        mag_dir, issue_name, fname = parts[0], parts[1], parts[2]
        if mag_dir not in mag_dirs:
            continue
        if not fname.lower().endswith(".epub"):
            continue
        m = DATE_RE.search(issue_name)
        if not m:
            continue
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          tzinfo=timezone.utc)
        except ValueError:
            continue
        issues[mag_dir][issue_name] = (dt, RAW_BASE + "/" + path)
    return issues


def download_epub(url):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "awesome-english-ebooks-rss-generator")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except Exception as e:
        sys.stderr.write("   下载 epub 失败: %s\n" % e)
        return None


def extract_articles(epub_bytes):
    """
    解压 epub，按 spine 顺序识别单篇文章。
    跳过：广告页、封面、目录/分隔页（纯文字 < MIN_ARTICLE_CHARS）。
    返回 [(文章标题, 正文HTML), ...]
    """
    z = zipfile.ZipFile(io.BytesIO(epub_bytes))
    container = z.read("META-INF/container.xml").decode("utf-8", "ignore")
    opf_path = re.search(r'full-path="([^"]+)"', container).group(1)
    opf = z.read(opf_path).decode("utf-8", "ignore")
    opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""

    manifest = {}
    for m in re.finditer(r"<item\b([^>]*?)/?>", opf):
        a = m.group(1)
        idm = re.search(r'id="([^"]+)"', a)
        hrem = re.search(r'href="([^"]+)"', a)
        if idm and hrem:
            manifest[idm.group(1)] = hrem.group(1)
    spine_ids = re.findall(r'<itemref[^>]*idref="([^"]+)"', opf)

    articles = []
    for sid in spine_ids:
        href = manifest.get(sid)
        if not href:
            continue
        path = (opf_dir + href) if not href.startswith("/") else href[1:]
        try:
            html = z.read(path).decode("utf-8", "ignore")
        except KeyError:
            continue

        low = html.lower()
        # 跳过广告页
        if "ad_h1" in low or "ad_div" in low or "ereader.link" in low or "ad_page" in low:
            continue
        # 跳过封面
        if "cover" in href.lower() and len(html) < 1500:
            continue
        # 跳过目录页
        if "toc" in href.lower() or 'class="toc' in low or "sec_index" in low:
            continue

        # 提取标题：优先 h1
        h1m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        h1 = re.sub(r"<[^>]+>", "", h1m.group(1)).strip() if h1m else ""

        # 纯文字量（去 script/style/标签）
        stripped = re.sub(r"<script\b.*?</script>", "", html, flags=re.S)
        stripped = re.sub(r"<style\b.*?</style>", "", stripped, flags=re.S)
        text = re.sub(r"<[^>]+>", "", stripped)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < MIN_ARTICLE_CHARS:
            continue  # 目录/分隔页

        title = h1
        if not title:
            tm = re.search(r"<title>([^<]+)</title>", html)
            t = tm.group(1) if tm else ""
            if t and not t.endswith(".html"):
                title = t
        if not title:
            title = "(无标题)"

        # 正文：取 body，清洗
        bm = re.search(r"<body[^>]*>(.*?)</body>", html, re.S)
        body = bm.group(1) if bm else stripped
        body = re.sub(r"<script\b.*?</script>", "", body, flags=re.S)
        body = re.sub(r"<style\b.*?</style>", "", body, flags=re.S)
        body = re.sub(r"<img[^>]*/?>", "", body)
        body = re.sub(r'href="[^"]*\.html[^"]*"', "", body)

        # 截断到段落边界
        if len(body) > ARTICLE_MAX_CHARS:
            cut = body.rfind("</p>", 0, ARTICLE_MAX_CHARS)
            if cut < ARTICLE_MAX_CHARS * 0.5:
                cut = ARTICLE_MAX_CHARS
            body = body[:cut + 4] + "<p>…（正文已截断）</p>"

        articles.append((title, body))
    return articles


def build_article_item(mag_name, issue_name, dt, seq, title, body):
    """单篇文章一条 RSS item。"""
    pub_date = format_datetime(dt.astimezone(timezone.utc))
    guid = "%s/%d/%s" % (issue_name, seq, BRANCH)
    desc = "<p>%s · %s</p>%s" % (escape(mag_name), escape(issue_name), body)
    return """
    <item>
      <title>%s</title>
      <guid isPermaLink="false">%s</guid>
      <pubDate>%s</pubDate>
      <description><![CDATA[%s]]></description>
    </item>""" % (
        escape(title),
        guid,
        pub_date,
        desc,
    )


def build_feed(title, desc, items_xml):
    now = format_datetime(datetime.now(timezone.utc))
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>%s</title>
    <link>%s</link>
    <description>%s</description>
    <language>zh-CN</language>
    <lastBuildDate>%s</lastBuildDate>
    <generator>awesome-english-ebooks-rss</generator>
%s
  </channel>
</rss>
""" % (escape(title), escape(FEED_LINK), escape(desc), now, items_xml)


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "feeds"
    os.makedirs(out_dir, exist_ok=True)

    sys.stderr.write(">> 拉取文件树 ...\n")
    paths = api_get_tree()
    if not paths:
        sys.stderr.write(">> 未获取到文件树，终止。\n")
        sys.exit(1)
    sys.stderr.write("   共 %d 个文件\n" % len(paths))

    issues = collect_issues(paths)

    for mag_dir, (feed_name, mag_name) in MAGAZINES.items():
        mag_issues = issues.get(mag_dir, {})
        sys.stderr.write(">> %s: %d 期\n" % (mag_dir, len(mag_issues)))
        sorted_issues = sorted(mag_issues.items(), key=lambda kv: kv[1][0], reverse=True)
        sorted_issues = sorted_issues[:PER_MAGAZINE_LIMIT]

        items_xml = ""
        total_articles = 0
        for issue_name, (dt, epub_url) in sorted_issues:
            sys.stderr.write("   下载 %s ...\n" % issue_name)
            data = download_epub(epub_url)
            if not data:
                continue
            try:
                articles = extract_articles(data)
            except Exception as e:
                sys.stderr.write("   提取失败 %s: %s\n" % (issue_name, e))
                continue
            sys.stderr.write("   %s: %d 篇文章\n" % (issue_name, len(articles)))
            for seq, (title, body) in enumerate(articles):
                items_xml += build_article_item(mag_name, issue_name, dt, seq, title, body)
                total_articles += 1

        feed = build_feed(
            "%s 文章更新" % mag_name,
            "%s 最新文章，点开标题直接阅读正文" % mag_name,
            items_xml,
        )
        out_path = os.path.join(out_dir, feed_name + ".xml")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(feed)
        sys.stderr.write(">> %s: %d 篇文章 -> %s\n" % (mag_name, total_articles, out_path))


if __name__ == "__main__":
    main()
