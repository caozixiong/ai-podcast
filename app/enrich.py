"""给入选素材补原文，并按条数上限挑选本集素材。

- arXiv：直接用 API 返回的完整摘要（fetch_status=abstract）。
- 指向发布方的直链（fetch_status=pending）：用诚实 UA 抓网页、抽正文；
  抓不到、太短或遇到付费墙 → 退回 RSS 摘要或"仅标题"。
- Google News 跳转链接：不解码、不抓取，只用标题（fetch_status=title）。
网页正文是不可信输入，只作为事实来源，不执行其中任何"指令"。
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .collect import UA, is_google_news

FETCH_TIMEOUT = 20
MIN_FULLTEXT_CHARS = 150     # 公司公告类简讯可能只有一两百字（如格隆汇快讯）
MAX_TEXT_CHARS = 8000
MIN_SUMMARY_CHARS = 200

PAYWALL_MARKERS = (
    "本文限", "会员阅读", "登入会员", "登录后阅读", "登录后查看全文", "订阅后阅读", "付费阅读",
    "subscribe to continue", "subscribers only", "to continue reading", "sign in to read",
)

_DROP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form",
              "iframe", "svg", "button")


def _densest_block(soup):
    best, best_len = None, 0
    for node in soup.find_all(["div", "section", "main"]):
        n = sum(len(p.get_text(strip=True)) for p in node.find_all("p", recursive=False))
        if n > best_len:
            best, best_len = node, n
    return best if best_len >= 200 else None


def extract_text(html: str) -> str:
    """从文章网页里抽正文（段落为主），去掉导航、页脚、脚本等。"""
    soup = BeautifulSoup(html or "", "html.parser")
    for t in soup(_DROP_TAGS):
        t.decompose()
    root = soup.find("article") or _densest_block(soup) or soup.body or soup
    paras = [p.get_text(" ", strip=True) for p in root.find_all(["p", "h2", "h3", "li"])]
    paras = [p for p in paras if len(p) >= 15]
    text = "\n".join(paras) if paras else root.get_text("\n", strip=True)
    text = re.sub(r"[ \t　\xa0]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text[:MAX_TEXT_CHARS]


def is_paywalled(text: str) -> bool:
    head = (text or "")[:2000].lower()
    return any(m.lower() in head for m in PAYWALL_MARKERS)


def fetch_article(url: str, *, get=requests.get, timeout: int = FETCH_TIMEOUT) -> tuple[str, str]:
    """返回 (fetch_status, text)；fetch_status 为 fulltext 或 title。"""
    host = (urlparse(url or "").hostname or "").lower()
    if not host or is_google_news(url):
        return "title", ""
    try:
        r = get(url, headers=UA, timeout=timeout, allow_redirects=True)
        ctype = (r.headers.get("Content-Type") or "").lower()
        if r.status_code != 200 or "html" not in ctype:
            return "title", ""
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding
        text = extract_text(r.text)
    except Exception:
        return "title", ""
    if len(text) < MIN_FULLTEXT_CHARS or is_paywalled(text):
        return "title", ""
    return "fulltext", text


def select_items(items: list[dict], *, max_items: int = 6, max_headline_items: int = 2,
                 fetch=fetch_article) -> list[dict]:
    """按原顺序（相关度优先）挑素材：总数不超过 max_items，"仅标题"的不超过 max_headline_items。

    返回的副本带 text（供写事实卡用，不入库）与最终 fetch_status。
    """
    selected: list[dict] = []
    headlines = 0
    for raw in items:
        if len(selected) >= max_items:
            break
        if not raw.get("url") or raw.get("relevance", 0) < 0:
            continue
        it = dict(raw)
        status = it.get("fetch_status") or "title"
        text = ""
        if status == "pending":
            status, text = fetch(it.get("direct_url") or it["url"])
            if status != "fulltext":
                if len(it.get("summary", "")) >= MIN_SUMMARY_CHARS:
                    status, text = "summary", it["summary"]
                else:
                    status = "title"
        elif status in ("abstract", "summary"):
            text = it.get("summary", "")
        if status == "title":
            if headlines >= max_headline_items:
                continue
            headlines += 1
            text = ""
        it["fetch_status"] = status
        it["text"] = text
        selected.append(it)
    return selected
