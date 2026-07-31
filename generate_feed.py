#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为 hehonghui/awesome-english-ebooks 生成 RSS 2.0 订阅源。

每期杂志一条 item，正文（description）直接内嵌从 epub 提取的文章 HTML，
在 RSS 阅读器里点开标题即可阅读正文，无需下载文件。

工作原理：
1. GitHub Git Tree API 一次性拉取整棵文件树
2. 过滤各杂志目录下的 .epub 文件
3. 对每期 epub：下载 -> 解压 -> 按 spine 顺序提取正文 HTML -> 去图片/广告页 -> 截断
4. 把正文塞进 RSS description，按期号日期降序生成 feed

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
MAGAZINES = {
    "01_economist": "经济学人 The Economist",
    "02_new_yorker": "纽约客 The New Yorker",
    "04_atlantic": "大西洋月刊 The Atlantic",
    "05_wired": "连线 Wired",
}
# 每个杂志保留最近多少期（每期会下载并解压一本 epub，期数越多 feed 越大、构建越慢）
PER_MAGAZINE_LIMIT = int(os.environ.get("PER_MAGAZINE_LIMIT", "2"))
# 单期正文 HTML 截断到多少字符（控制单条体积；整本全文会让 feed 过大被阅读器拒收）
MAX_CONTENT_CHARS = int(os.environ.get("MAX_CONTENT_CHARS", "30000"))
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
TREE_API = f"https://api.github.com/repos/{REPO}/git/trees/{BRANCH}?recursive=1"
FEED_TITLE = "awesome-english-ebooks 外刊更新"
FEED_DESC = "经济学人、纽约客、大西洋月刊、连线等英语外刊杂志更新（点开标题直接阅读正文）"
FEED_LINK = "https://github.com/" + REPO

DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")
VALID_EXT = (".epub",)


def api_get_tree():
    """调用 Git Tree API，返回路径列表。带 token 则认证。"""
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
            sys.stderr.write("GitHub API 限流，请设置 GITHUB_TOKEN 环境变量后重试。\n")
        else:
            sys.stderr.write("API 调用失败 [%s]\n" % e.code)
        return None
    except Exception as e:
        sys.stderr.write("请求异常: %s\n" % e)
        return None


def collect_issues(paths):
    """
    从全部路径中提取每期文件。
    返回: { mag_dir: { issue_name: (datetime, [(fname, raw_url), ...]) } }
    路径形如: 01_economist/te_2026.07.25/TheEconomist.2026.07.25.epub
    """
    mag_dirs = set(MAGAZINES.keys())
    issues = defaultdict(dict)
    for path in paths:
        parts = path.split("/")
        if len(parts) < 3:
            continue
        mag_dir, issue_name, fname = parts[0], parts[1], parts[2]
        if mag_dir not in mag_dirs:
            continue
        if not fname.lower().endswith(VALID_EXT):
            continue
        m = DATE_RE.search(issue_name)
        if not m:
            continue
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          tzinfo=timezone.utc)
        except ValueError:
            continue
        raw_url = RAW_BASE + "/" + path
        issue = issues[mag_dir].get(issue_name)
        if issue is None:
            issue = (dt, [])
            issues[mag_dir][issue_name] = issue
        issue[1].append((fname, raw_url))
    return issues


def download_epub(url):
    """下载 epub，返回 bytes。失败返回 None。"""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "awesome-english-ebooks-rss-generator")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except Exception as e:
        sys.stderr.write("   下载 epub 失败: %s\n" % e)
        return None


def extract_epub_html(epub_bytes, max_chars):
    """
    解压 epub，按 spine 顺序提取正文 HTML。
    - 跳过广告页（含 ad_h1/ad_div/ereader.link 特征）
    - 去掉 script/style/img（图片相对路径在 RSS 里失效）
    - 去掉内部 .html 跳转链接的 href（保留文字）
    - 按段落边界截断到 max_chars
    """
    z = zipfile.ZipFile(io.BytesIO(epub_bytes))
    container = z.read("META-INF/container.xml").decode("utf-8", "ignore")
    opf_path = re.search(r'full-path="([^"]+)"', container).group(1)
    opf = z.read(opf_path).decode("utf-8", "ignore")
    opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""

    # manifest: id -> href（兼容属性顺序）
    manifest = {}
    for m in re.finditer(r"<item\b([^>]*?)/?>", opf):
        attrs = m.group(1)
        idm = re.search(r'id="([^"]+)"', attrs)
        hrem = re.search(r'href="([^"]+)"', attrs)
        if idm and hrem:
            manifest[idm.group(1)] = hrem.group(1)
    spine_ids = re.findall(r'<itemref[^>]*idref="([^"]+)"', opf)

    parts = []
    total = 0
    for sid in spine_ids:
        href = manifest.get(sid)
        if not href:
            continue
        path = (opf_dir + href) if not href.startswith("/") else href[1:]
        try:
            html = z.read(path).decode("utf-8", "ignore")
        except KeyError:
            continue
        # 跳过广告页
        if "ad_h1" in html or "ad_div" in html or "ereader.link" in html:
            continue
        bm = re.search(r"<body[^>]*>(.*?)</body>", html, re.S)
        body = bm.group(1) if bm else html
        # 清洗：去 script/style/img；去掉内部 .html 跳转 href
        body = re.sub(r"<script\b.*?</script>", "", body, flags=re.S)
        body = re.sub(r"<style\b.*?</style>", "", body, flags=re.S)
        body = re.sub(r"<img[^>]*/?>", "", body)
        body = re.sub(r'href="[^"]*\.html[^"]*"', "", body)
        parts.append(body)
        total += len(body)
        if total >= max_chars:
            break

    merged = "".join(parts)
    if len(merged) > max_chars:
        cut = merged.rfind("</p>", 0, max_chars)
        if cut < max_chars * 0.5:
            cut = max_chars
        merged = merged[:cut + 4] + "<p>…（正文已截断，完整内容请下载 epub）</p>"
    return merged


def build_item(mag_dir, mag_name, issue_name, dt, files):
    """构造单条 RSS item。下载 epub 提取正文塞进 description，点开标题即可阅读。"""
    title = "%s %s" % (mag_name, issue_name)
    epub_url = files[0][1] if files else ""
    pub_date = format_datetime(dt.astimezone(timezone.utc))

    # 下载 epub 并提取正文
    body_html = ""
    if epub_url:
        sys.stderr.write("   下载并提取 %s ...\n" % issue_name)
        data = download_epub(epub_url)
        if data:
            try:
                body_html = extract_epub_html(data, MAX_CONTENT_CHARS)
            except Exception as e:
                sys.stderr.write("   提取正文失败: %s\n" % e)

    if body_html:
        desc = "<p>%s 第 %s 期正文：</p>" % (escape(mag_name), escape(issue_name)) + body_html
    else:
        desc = "<p>%s 第 %s 期（正文提取失败，<a href=\"%s\">点此下载 epub</a>）</p>" % (
            escape(mag_name), escape(issue_name), escape(epub_url))

    enclosure = ""
    if epub_url:
        enclosure = '<enclosure url="%s" type="application/epub" length="0" />' % escape(epub_url)

    return """
    <item>
      <title>%s</title>
      <link>%s</link>
      <guid isPermaLink="false">%s/%s/%s</guid>
      <pubDate>%s</pubDate>
      <description><![CDATA[%s]]></description>
      %s
    </item>""" % (
        escape(title), escape(epub_url),
        mag_dir, issue_name, BRANCH,
        pub_date,
        desc,
        enclosure,
    )


def build_feed(items_xml):
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
""" % (escape(FEED_TITLE), escape(FEED_LINK), escape(FEED_DESC), now, items_xml)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "feed.xml"
    sys.stderr.write(">> 拉取文件树 ...\n")
    paths = api_get_tree()
    if not paths:
        sys.stderr.write(">> 未获取到文件树，终止。\n")
        sys.exit(1)
    sys.stderr.write("   共 %d 个文件\n" % len(paths))

    issues = collect_issues(paths)
    all_items = []
    for mag_dir, mag_name in MAGAZINES.items():
        mag_issues = issues.get(mag_dir, {})
        sys.stderr.write(">> %s: %d 期\n" % (mag_dir, len(mag_issues)))
        # 按日期降序，取最近 N 期
        sorted_issues = sorted(mag_issues.items(), key=lambda kv: kv[1][0], reverse=True)
        for issue_name, (dt, files) in sorted_issues[:PER_MAGAZINE_LIMIT]:
            if not files:
                continue
            all_items.append((dt, build_item(mag_dir, mag_name, issue_name, dt, files)))

    # 所有杂志混排，按期号日期降序
    all_items.sort(key=lambda x: x[0], reverse=True)
    items_xml = "".join(x[1] for x in all_items)
    feed = build_feed(items_xml)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(feed)
    sys.stderr.write(">> 已生成 %s（共 %d 条）\n" % (out_path, len(all_items)))


if __name__ == "__main__":
    main()
