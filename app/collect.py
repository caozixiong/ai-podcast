"""采集公开新闻/学术素材（RSS / Atom / arXiv）。

输入源均为公开（RSS/Atom/arXiv）。输出是归一化的 Item 列表。
**不生成**输出端 Podcast RSS。论文/新闻正文是不可信输入，只做摘要提取。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from dateutil import parser as dtparser

TIMEOUT = 30
UA = {"User-Agent": "ai-podcast-collector/0.1 (public-news-ingest)"}


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


def _clean(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:600]


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
        try:
            if src.get("type") == "arxiv":
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
            title = _clean(e.get("title", ""))
            link = (e.get("link") or "").strip()
            summary = _clean(e.get("summary", "") or e.get("description", ""))
            pub = _parse_pub(e)
            if not (title and link):
                continue
            # 有明确日期但超出时间窗 → 丢弃，避免播报旧新闻；无日期的保留（后续按相关度排）
            if pub is not None and pub < cutoff:
                continue
            key = _norm_id(str(e.get("id", "")), link, title)
            if key in seen:
                continue
            seen.add(key)

            blob = (title + " " + summary).lower()
            score = sum(1 for k in pkeys if k in blob)
            items.append({
                "id": key,
                "title": title,
                "url": link,
                "source_name": src.get("name", ""),
                "published": pub.strftime("%Y-%m-%d") if pub else "",
                "summary": summary,
                "relevance": score,
            })

    items.sort(key=lambda x: (x["relevance"], x["published"]), reverse=True)
    if errors:
        items = items[:max_items]
        items.append({"id": "_errors", "title": "采集警告", "url": "",
                      "source_name": "", "published": "",
                      "summary": " | ".join(errors)[:400], "relevance": -1})
    return items[:max_items]
