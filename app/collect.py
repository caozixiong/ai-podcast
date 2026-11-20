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
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

import feedparser
import requests
from dateutil import parser as dtparser

TIMEOUT = 30
UA = {"User-Agent": "ai-podcast/1.0 (research; contact: caozixiong@users.noreply.github.com)"}

ARXIV_ABSTRACT_LIMIT = 4000     # arXiv 摘要基本都在 2000 字符内；不再截成 600
NEWS_SUMMARY_LIMIT = 1500
MIN_ABSTRACT_CHARS = 200
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_MIN_INTERVAL = 3.2        # arXiv 要求同一客户端 3 秒内不超过 1 次请求
ARXIV_RETRY_WAIT = 8            # 限流/临时故障时只重试一次
TITLE_FACTOR = 2                # 关键词出现在标题里，比只在摘要里提到一句更能说明切题


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


_SEO_TAIL = re.compile(r"(?:_[^_\s]{1,12})+$")
_LEAD_TAG = re.compile(r"^(?:\s*(?:【[^】]{1,20}】|\[[^\]]{1,20}\]))+")


def _strip_seo_tail(title: str) -> str:
    """新闻标题常带站点栏目尾巴：『…封测年会_华汉伟业_半导体』『…开工_市场测评_市场_中金在线』。"""
    cut = _SEO_TAIL.sub("", title).strip()
    return cut if len(cut) >= 8 else title


def title_key(title: str) -> str:
    """判断"是不是同一条新闻"用的标题指纹：去掉【栏目】前缀、栏目尾巴、标点和空白。"""
    t = _LEAD_TAG.sub("", _strip_seo_tail(title or ""))
    return re.sub(r"[\W_]+", "", t).lower()


def _kw_pattern(kw: str) -> re.Pattern:
    """关键词 → 正则。

    - 拉丁字母/数字开头或结尾的一侧，不得紧挨别的字母数字：'SEM' 不再命中 'self-assembly'，
      而 '量产级TGV全工艺' 里的 TGV 仍能命中（两侧是汉字）。
    - 全大写缩写（SEM、CT、TSV）区分大小写；其余不区分。结尾是字母时允许复数 s。
    - 空格和连字符可有可无：'non-destructive' 也命中 'nondestructive'、'non destructive'。
    """
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", kw.strip()) if p]
    body = r"[\s\-]*".join(parts)
    left = r"(?<![A-Za-z0-9])" if re.match(r"[A-Za-z0-9]", kw.strip()) else ""
    tail = kw.strip()[-1:]
    right = ""
    if re.match(r"[A-Za-z]", tail):
        right = r"s?(?![A-Za-z0-9])"
    elif tail.isdigit():
        right = r"(?![A-Za-z0-9])"
    letters = re.sub(r"[^A-Za-z]", "", kw)
    acronym = bool(letters) and letters.isupper() and len(letters) <= 5
    return re.compile(left + body + right, 0 if acronym else re.IGNORECASE)


class RelevanceScorer:
    """按主题给素材打相关度。

    sources.json 的 topics：[{name, weight, core, keywords}]。每个主题只计一次分：
    关键词在标题里命中记 weight×TITLE_FACTOR，只在摘要里命中记 weight。
    关键词前加 ^ 表示只认标题：单独一个 "X-ray" 出现在摘要里多半是 X 射线衍射、能谱之类的表征手段，
    不代表这篇论文是讲 X 射线成像的。
    标了 core 的主题一个都没命中 → 0 分（只沾边、不切题）。
    medical_exclusion：医学影像不播。标题里出现医学词 → 0 分；摘要里出现解剖/病患类的强词，
    或两个以上不同的医学词 → 0 分。例外：标题本身命中了 industrial 主题（工业论文顺带提一句
    "不同于患者成像"），或摘要里只有一个泛词（medical、clinical）→ 只扣 penalty 分，不排除。
    exclude_title_keywords：标题里出现任一词直接记 0 分（市场报告类软文、借"无损检测"之名的无关消费资讯）。
    没有 topics 时退回旧的 priority_keywords 平铺列表（全部算核心、权重 1）。
    """

    def __init__(self, sources_cfg: dict | None = None, priority_keywords: list[str] | None = None):
        cfg = sources_cfg or {}
        topics = cfg.get("topics") or []
        if not topics:
            topics = [{"name": "关键词", "weight": 1, "core": True,
                       "keywords": list(priority_keywords or [])}]
        self.topics = [{"name": t.get("name", ""), "weight": int(t.get("weight", 1)),
                        "core": bool(t.get("core", False)), "industrial": bool(t.get("industrial", False)),
                        "patterns": [(k.lstrip("^"), _kw_pattern(k.lstrip("^")), k.startswith("^"))
                                     for k in t.get("keywords", []) if k.strip("^ ")]}
                       for t in topics]
        self.need_core = any(t["core"] for t in self.topics)
        med = cfg.get("medical_exclusion") or {}
        self.penalty = int(med.get("penalty", 0))
        self.med_strong = [(k, _kw_pattern(k)) for k in med.get("strong_keywords", []) if k.strip()]
        self.med_weak = [(k, _kw_pattern(k)) for k in med.get("weak_keywords", []) if k.strip()]
        self.exclude = [(k, _kw_pattern(k)) for k in cfg.get("exclude_title_keywords", []) if k.strip()]

    def score(self, title: str, summary: str = "") -> tuple[int, list[str]]:
        """返回 (相关度, 命中说明)。命中说明形如『先进封装@标题:TGV』，入库后便于事后查为什么选它。"""
        total, hits, core_hit = 0, [], False
        banned = next((k for k, p in self.exclude if p.search(title or "")), None)
        if banned:
            return 0, [f"排除:{banned}"]
        industrial_title = False
        for t in self.topics:
            in_title = next((k for k, p, _ in t["patterns"] if p.search(title or "")), None)
            in_body = None if in_title else next(
                (k for k, p, title_only in t["patterns"] if not title_only and p.search(summary or "")), None)
            if in_title:
                total += t["weight"] * TITLE_FACTOR
                hits.append(f"{t['name']}@标题:{in_title}")
                industrial_title = industrial_title or t["industrial"]
            elif in_body:
                total += t["weight"]
                hits.append(f"{t['name']}@摘要:{in_body}")
            else:
                continue
            core_hit = core_hit or t["core"]
        verdict, words = self._medical(title or "", summary or "", industrial_title)
        if verdict == "exclude":
            return 0, [f"排除(医学影像):{'、'.join(words)}"]
        if self.need_core and not core_hit:
            return 0, hits
        if verdict == "penalty" and self.penalty:
            total = max(0, total - self.penalty)
            hits.append(f"降权(提到医学):{'、'.join(words)}")
        return total, hits

    def _medical(self, title: str, summary: str, industrial_title: bool) -> tuple[str, list[str]]:
        """判断是不是医学影像素材：返回 ('exclude' | 'penalty' | '', 命中的词)。"""
        both = self.med_strong + self.med_weak
        in_title = [k for k, p in both if p.search(title)]
        if in_title:
            return "exclude", in_title[:3]
        strong = [k for k, p in self.med_strong if p.search(summary)]
        weak = [k for k, p in self.med_weak if p.search(summary)]
        if not strong and not weak:
            return "", []
        if len(strong) + len(weak) >= 2 and not (industrial_title and len(strong) <= 1 and not weak):
            return "exclude", (strong + weak)[:3]
        if strong and not industrial_title:
            return "exclude", strong[:3]
        return "penalty", (strong + weak)[:3]


def arxiv_url(src: dict) -> str:
    """arXiv 源可以直接给 url，也可以给人能读的 query（由这里拼成 API 地址）。"""
    if src.get("query"):
        return (f"{ARXIV_API}?search_query={quote(src['query'], safe=':()')}&start=0"
                f"&max_results={int(src.get('max_results', 30))}"
                "&sortBy=submittedDate&sortOrder=descending")
    return src.get("url", "")


def _get_arxiv(url: str, state: dict):
    """取 arXiv 列表：两次请求之间至少隔 ARXIV_MIN_INTERVAL 秒；限流或临时故障只重试一次。"""
    last_err = None
    for attempt in range(2):
        wait = ARXIV_MIN_INTERVAL - (time.monotonic() - state["last"]) if "last" in state else 0
        if attempt:
            wait = max(wait, ARXIV_RETRY_WAIT)
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, timeout=TIMEOUT, headers=UA)
            state["last"] = time.monotonic()
            r.raise_for_status()
            return r
        except Exception as e:            # noqa: BLE001 —— 记下原因，交给上层决定跳过这个源
            state["last"] = time.monotonic()
            last_err = e
    raise last_err


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
                    max_items: int = 40, news_lookback_days: int | None = None) -> list[dict]:
    """返回归一化 Item（相关度/新度降序）。不可用源会被跳过并记录。"""
    scorer = RelevanceScorer(sources_cfg, priority_keywords)
    arxiv_state: dict = {}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    # 新闻过期得比论文快：三周前的旧闻不该当成今天的资讯来念
    news_cutoff = now - timedelta(days=news_lookback_days) if news_lookback_days else cutoff
    items: list[dict] = []
    seen = set()
    errors: list[str] = []

    for src in sources_cfg.get("sources", []):
        if not src.get("enabled", True):
            continue
        is_arxiv = src.get("type") == "arxiv"
        url = arxiv_url(src) if is_arxiv else src.get("url")
        if not url:
            continue
        try:
            if is_arxiv:
                r = _get_arxiv(url, arxiv_state)
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
            if src.get("type") != "arxiv":
                title = _strip_seo_tail(title)
            raw_summary = e.get("summary", "") or e.get("description", "")
            pub = _parse_pub(e)
            if not (title and link):
                continue
            # 有明确日期但超出时间窗 → 丢弃，避免播报旧新闻；无日期的保留（后续按相关度排）
            if pub is not None and pub < (cutoff if is_arxiv else news_cutoff):
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

            score, hits = scorer.score(title, summary)
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
                "relevance_hits": hits,
            })

    items.sort(key=lambda x: (x["relevance"], x["published"]), reverse=True)
    if errors:
        items = items[:max_items]
        items.append({"id": "_errors", "title": "采集警告", "url": "",
                      "source_name": "", "published": "",
                      "summary": " | ".join(errors)[:400], "relevance": -1})
    return items[:max_items]
