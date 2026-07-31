#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为 hehonghui/awesome-english-ebooks 生成带 epub/mobi/pdf 直链的 RSS 2.0 订阅源。

工作原理：
1. 通过 GitHub Git Tree API 一次性拉取整棵文件树（递归）
2. 过滤出各杂志目录下的 .epub/.mobi/.pdf 文件
3. 按"期"分组，合并所有杂志，按期号日期降序生成单个 RSS 2.0 feed

依赖：仅标准库。GITHUB_TOKEN 环境变量可选（有则提高 API 限额到 5000/小时）。
单次 tree API 调用即可拿到全部路径，几乎不会触发限流。
"""

import os
import re
import sys
import json
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

# ---- 配置 ----
REPO = "hehonghui/awesome-english-ebooks"
BRANCH = "master"
# 杂志目录 -> 杂志中文名（用于 feed 标题展示）
MAGAZINES = {
    "01_economist": "经济学人 The Economist",
    "02_new_yorker": "纽约客 The New Yorker",
    "04_atlantic": "大西洋月刊 The Atlantic",
    "05_wired": "连线 Wired",
}
# 每个杂志最多保留最近多少期（控制 feed 体积）
PER_MAGAZINE_LIMIT = int(os.environ.get("PER_MAGAZINE_LIMIT", "20"))
# 直链使用 raw.githubusercontent.com（对大文件最稳定）
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
# Git Tree API：recursive=1 递归拉取整棵树
TREE_API = f"https://api.github.com/repos/{REPO}/git/trees/{BRANCH}?recursive=1"
FEED_TITLE = "awesome-english-ebooks 外刊更新"
FEED_DESC = "经济学人、纽约客、大西洋月刊、连线等英语外刊杂志更新（epub/mobi/pdf）"
FEED_LINK = "https://github.com/" + REPO

DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")
VALID_EXT = (".epub", ".mobi", ".pdf")


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


def build_item(mag_dir, mag_name, issue_name, dt, files):
    """构造单条 RSS item。每期一条，描述里列出三种格式直链。"""
    title = "%s %s" % (mag_name, issue_name)
    link = "https://github.com/%s/tree/%s/%s/%s" % (REPO, BRANCH, mag_dir, issue_name)
    pub_date = format_datetime(dt.astimezone(timezone.utc))

    lines = ["<p>%s 第 %s 期，共 %d 个文件：</p><ul>" % (escape(mag_name), escape(issue_name), len(files))]
    for fname, url in files:
        ext = os.path.splitext(fname)[1].lstrip(".").upper()
        lines.append('<li><a href="%s">%s (%s)</a></li>' % (escape(url), escape(fname), ext))
    lines.append("</ul>")

    # enclosure 用第一个文件（通常是 epub），便于支持 enclosure 的阅读器显示下载
    enclosure = ""
    if files:
        fname, url = files[0]
        enclosure = '<enclosure url="%s" type="application/%s" length="0" />' % (
            escape(url), os.path.splitext(fname)[1].lstrip("."))

    return """
    <item>
      <title>%s</title>
      <link>%s</link>
      <guid isPermaLink="false">%s/%s/%s</guid>
      <pubDate>%s</pubDate>
      <description><![CDATA[%s]]></description>
      %s
    </item>""" % (
        escape(title), escape(link),
        mag_dir, issue_name, BRANCH,
        pub_date,
        "".join(lines),
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
            # 按格式排序：epub, mobi, pdf
            order = {".epub": 0, ".mobi": 1, ".pdf": 2}
            files.sort(key=lambda x: order.get(os.path.splitext(x[0])[1].lower(), 9))
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
