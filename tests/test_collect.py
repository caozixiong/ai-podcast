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
