"""采集公开新闻/学术素材（RSS / Atom / arXiv）。

输入源均为公开（RSS/Atom/arXiv）。输出是归一化的 Item 列表。
**不生成**输出端 Podcast RSS。论文/新闻正文是不可信输入，只做摘要提取。

每条 Item 记录：
  publisher     真实发布方（Google News 的 <source>、arXiv），而不是 RSS 频道名
  kind          arxiv | news
  fetch_status  abstract（arXiv 完整摘要）| title（只有标题，如 Google News 跳转链接）
                | pending（直链，入选后由 enrich 抓正文）
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests
from dateutil import parser as dtparser

TIMEOUT = 30
UA = {"User-Agent": "ai-podcast/1.0 (research; contact: caozixiong@users.noreply.github.com)"}

ARXIV_ABSTRACT_LIMIT = 4000     # arXiv 摘要基本都在 2000 字符内；不再截成 600
NEWS_SUMMARY_LIMIT = 1500
MIN_ABSTRACT_CHARS = 200


def _norm_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _parse_pub(entry) -> datetime | None:
    for k in ("published", "updated"):
        v = entry.get(k)
        if v:
            try:
                return dtparser.parse(v).astimezone(timezone.utc)
            except Exception:
                pass
    return None


def _legacy_key_title(text: str) -> str:
    """去重 id 用的标题：与旧版 _clean 完全一致（含『 - 发布方』尾巴），保证已播条目的 id 不变。"""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:600]


def _clean(text: str, limit: int | None = 600) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if limit else text


def is_google_news(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == "news.google.com" or host.endswith(".news.google.com")


def _publisher(entry, src: dict) -> str:
    s = entry.get("source")
    if isinstance(s, dict) and (s.get("title") or "").strip():
        return s["title"].strip()
    if src.get("type") == "arxiv":
        return "arXiv"
    return (src.get("publisher") or "").strip()


def _strip_publisher_suffix(title: str, publisher: str) -> str:
    """Google News 标题形如『标题 - 发布方』，去掉尾巴，发布方单独存。"""
    if publisher and title.endswith(" - " + publisher):
        return title[: -(len(publisher) + 3)].strip()
    return title


def _authors(entry) -> list[str]:
    out = []
    for a in entry.get("authors", []) or []:
        name = a.get("name") if isinstance(a, dict) else None
        if name:
            out.append(name.strip())
    return out[:8]


def arxiv_id_from_url(url: str) -> str:
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#/]+?)(?:\.pdf)?$", (url or "").strip())
    return m.group(1) if m else ""


def fetch_arxiv_by_ids(arxiv_ids: list[str]) -> dict[str, dict]:
    """按 arXiv id 一次性取完整摘要（公开 API，单次请求）。返回 {arxiv_id: {summary, authors, title}}。"""
    ids = [i for i in dict.fromkeys(arxiv_ids) if i]
    if not ids:
        return {}
    url = ("https://export.arxiv.org/api/query?id_list=" + ",".join(ids)
           + f"&max_results={len(ids)}")
    r = requests.get(url, timeout=TIMEOUT, headers=UA)
    r.raise_for_status()
    out = {}
    for e in feedparser.parse(r.content).get("entries", []):
        aid = arxiv_id_from_url(e.get("id") or e.get("link") or "")
        if aid:
            out[aid] = {"summary": _clean(e.get("summary", ""), ARXIV_ABSTRACT_LIMIT),
                        "authors": _authors(e), "title": _clean(e.get("title", ""), None)}
    return out


def collect_sources(sources_cfg: dict, *, lookback_days: int = 7,
                    priority_keywords: list[str] | None = None,
                    max_items: int = 40) -> list[dict]:
    """返回归一化 Item（相关度/新度降序）。不可用源会被跳过并记录。"""
    pkeys = [k.lower() for k in (priority_keywords or [])]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    items: list[dict] = []
    seen = set()
    errors: list[str] = []

    for src in sources_cfg.get("sources", []):
        if not src.get("enabled", True):
            continue
        url = src.get("url")
        if not url:
            continue
        is_arxiv = src.get("type") == "arxiv"
        try:
            if is_arxiv:
                r = requests.get(url, timeout=TIMEOUT, headers=UA)
                r.raise_for_status()
                feed = feedparser.parse(r.content)
            else:
                feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                errors.append(f"{src.get('name')}: {feed.bozo_exception}")
                continue
        except Exception as e:
            errors.append(f"{src.get('name')}: {e}")
            continue

        for e in feed.get("entries", []):
            link = (e.get("link") or "").strip()
            publisher = _publisher(e, src)
            title = _strip_publisher_suffix(_clean(e.get("title", ""), None), publisher)
            raw_summary = e.get("summary", "") or e.get("description", "")
            pub = _parse_pub(e)
            if not (title and link):
                continue
            # 有明确日期但超出时间窗 → 丢弃，避免播报旧新闻；无日期的保留（后续按相关度排）
            if pub is not None and pub < cutoff:
                continue
            key = _norm_id(str(e.get("id", "")), link, _legacy_key_title(e.get("title", "")))
            if key in seen:
                continue
            seen.add(key)

            if is_arxiv:
                kind = "arxiv"
                summary = _clean(raw_summary, ARXIV_ABSTRACT_LIMIT)
                status = "abstract" if len(summary) >= MIN_ABSTRACT_CHARS else "title"
            elif is_google_news(link):
                # Google News 的 summary 只是『标题 + 发布方』，没有正文信息
                kind, summary, status = "news", "", "title"
            else:
                kind = "news"
                summary = _clean(raw_summary, NEWS_SUMMARY_LIMIT)
                status = "pending"

            blob = (title + " " + summary).lower()
            score = sum(1 for k in pkeys if k in blob)
            items.append({
                "id": key,
                "title": title,
                "url": link,
                "source_name": src.get("name", ""),     # RSS 频道名：只用于内部追踪，不得念给听众
                "publisher": publisher,
                "kind": kind,
                "authors": _authors(e) if is_arxiv else [],
                "published": pub.strftime("%Y-%m-%d") if pub else "",
                "summary": summary,
                "fetch_status": status,
                "relevance": score,
            })

    items.sort(key=lambda x: (x["relevance"], x["published"]), reverse=True)
    if errors:
        items = items[:max_items]
        items.append({"id": "_errors", "title": "采集警告", "url": "",
                      "source_name": "", "published": "",
                      "summary": " | ".join(errors)[:400], "relevance": -1})
    return items[:max_items]
