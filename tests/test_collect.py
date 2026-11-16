import os, sys
from datetime import datetime, timedelta, timezone
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app.collect as c  # noqa


class FakeFeed:
    bozo = False

    def __init__(self, entries):
        self.entries = entries

    def get(self, k, d=None):
        return getattr(self, k, d)


def _fmt(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def test_filter_window_dedup_and_relevance(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = _fmt(now - timedelta(days=1))
    stale = _fmt(now - timedelta(days=30))
    entries = [
        {"title": "TSV 检测新进展", "link": "https://n.com/1",
         "summary": "through silicon via 先进封装 无损检测", "published": fresh},
        {"title": "TSV 检测新进展", "link": "https://n.com/1",
         "summary": "through silicon via", "published": fresh},   # 与第1条同 link+title → 应去重
        {"title": "无关新闻", "link": "https://n.com/2", "summary": "天气", "published": fresh},
        {"title": "旧新闻", "link": "https://n.com/3", "summary": "TSV", "published": stale},  # 过期
    ]
    monkeypatch.setattr(c.feedparser, "parse", lambda url: FakeFeed(entries))
    cfg = {"sources": [{"name": "S", "type": "rss", "url": "http://x/rss", "enabled": True}]}
    items = c.collect_sources(cfg, lookback_days=7,
                              priority_keywords=["TSV", "硅通孔", "封装"], max_items=20)
    real = [i for i in items if i.get("url")]
    titles = [i["title"] for i in real]

    assert "旧新闻" not in titles                                  # 过期被过滤
    assert titles.count("TSV 检测新进展") == 1                      # 同 link+title 去重
    assert "TSV 检测新进展" in titles
    assert titles.index("TSV 检测新进展") < titles.index("无关新闻")  # 相关度优先


def test_google_news_publisher_and_title_only(monkeypatch):
    fresh = _fmt(datetime.now(timezone.utc) - timedelta(days=1))
    entries = [{"title": "硅光芯片测试平台落地绍兴 - 电子工程专辑EE Times China",
                "link": "https://news.google.com/rss/articles/CBMiXYZ?oc=5",
                "summary": '<a href="https://news.google.com/rss/articles/CBMiXYZ">硅光芯片测试平台落地绍兴</a>'
                           '&nbsp;&nbsp;<font color="#6f6f6f">电子工程专辑EE Times China</font>',
                "source": {"href": "https://www.eet-china.com", "title": "电子工程专辑EE Times China"},
                "published": fresh}]
    monkeypatch.setattr(c.feedparser, "parse", lambda url: FakeFeed(entries))
    cfg = {"sources": [{"name": "Google News RSS: X", "type": "rss", "url": "http://x/rss"}]}
    it = c.collect_sources(cfg, lookback_days=7, priority_keywords=["硅光"])[0]
    assert it["publisher"] == "电子工程专辑EE Times China"
    assert it["title"] == "硅光芯片测试平台落地绍兴"              # 去掉『 - 发布方』尾巴
    assert it["fetch_status"] == "title" and it["summary"] == ""
    assert it["source_name"] == "Google News RSS: X"
    # 去重 id 必须沿用旧算法（含『 - 发布方』的原标题），否则已播新闻会被当成新的
    assert it["id"] == c._norm_id("", entries[0]["link"], entries[0]["title"])


def test_arxiv_abstract_not_truncated(monkeypatch):
    fresh = _fmt(datetime.now(timezone.utc) - timedelta(days=1))
    abstract = "Model order reduction for 2.5D/3D ICs. " * 40       # ~1600 字符，旧代码会截到 600
    entries = [{"title": "SPLIT-MOR", "link": "https://arxiv.org/abs/2611.17693v1",
                "summary": abstract, "published": fresh,
                "authors": [{"name": "Zebin Shi"}, {"name": "Runsheng Wang"}]}]

    class R:
        content = b""

        def raise_for_status(self):
            pass

    monkeypatch.setattr(c.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(c.feedparser, "parse", lambda data: FakeFeed(entries))
    cfg = {"sources": [{"name": "arXiv: X", "type": "arxiv", "url": "https://export.arxiv.org/api/query"}]}
    it = c.collect_sources(cfg, lookback_days=7, priority_keywords=["3D"])[0]
    assert len(it["summary"]) > 1500
    assert it["fetch_status"] == "abstract" and it["kind"] == "arxiv"
    assert it["publisher"] == "arXiv" and it["authors"] == ["Zebin Shi", "Runsheng Wang"]
