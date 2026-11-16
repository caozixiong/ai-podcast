"""正文抽取、付费墙识别与素材挑选（纯离线）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.enrich import extract_text, fetch_article, is_paywalled, select_items  # noqa

ARTICLE = """<html><head><script>var x=1;</script></head><body>
<nav><a>首页</a><a>新闻</a></nav>
<article><h1>标题</h1>
<p>11月6日，绍兴光电公共服务平台开放日在柯桥举行，标志着平台全面对外承接硅光芯片测试业务。</p>
<p>平台同时提供 TGV 玻璃基板封装与封装测试表征分析等公共服务，面向企业、高校及科研院所开放。</p>
<p>研究中心于2026年2月正式投运，总投资约2亿元，规划建筑面积约10000平方米。</p>
</article><footer>版权所有</footer></body></html>"""


class Resp:
    def __init__(self, text, status=200, ctype="text/html; charset=utf-8"):
        self.text, self.status_code = text, status
        self.headers = {"Content-Type": ctype}
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"


def test_extract_text_keeps_paragraphs_drops_chrome():
    text = extract_text(ARTICLE)
    assert "TGV 玻璃基板封装" in text and "2亿元" in text
    assert "var x" not in text and "版权所有" not in text and "首页" not in text


def test_paywall_detection():
    assert is_paywalled("本文限「Research」会员阅读，请登入会员")
    assert not is_paywalled("平台全面对外承接硅光芯片测试业务")


def test_fetch_article_google_news_is_title_only():
    def boom(*a, **k):
        raise AssertionError("Google News 跳转链接不应被抓取")
    assert fetch_article("https://news.google.com/rss/articles/CBMi?oc=5", get=boom) == ("title", "")


def test_fetch_article_fulltext_and_short_page():
    long_html = ARTICLE.replace("</article>", "<p>" + "测试内容。" * 80 + "</p></article>")
    st, text = fetch_article("https://www.eet-china.com/info/1.html", get=lambda *a, **k: Resp(long_html))
    assert st == "fulltext" and "TGV" in text
    st, _ = fetch_article("https://x.com/a", get=lambda *a, **k: Resp("<p>太短</p>"))
    assert st == "title"


def test_select_items_caps_total_and_headlines():
    items = [{"id": str(i), "title": f"新闻{i}", "url": f"https://news.google.com/{i}",
              "fetch_status": "title", "relevance": 5} for i in range(4)]
    items += [{"id": "a", "title": "论文", "url": "https://arxiv.org/abs/1", "kind": "arxiv",
               "fetch_status": "abstract", "summary": "abstract text " * 30, "relevance": 1}]
    picked = select_items(items, max_items=6, max_headline_items=2, fetch=lambda u: ("title", ""))
    statuses = [p["fetch_status"] for p in picked]
    assert statuses.count("title") == 2                     # 仅标题最多 2 条
    assert "abstract" in statuses and len(picked) == 3
    assert picked[-1]["text"].startswith("abstract text")


def test_select_items_pending_falls_back_to_summary():
    it = {"id": "p", "title": "直链新闻", "url": "https://example.com/a", "fetch_status": "pending",
          "summary": "这是一段足够长的 RSS 摘要。" * 20, "relevance": 1}
    picked = select_items([it], fetch=lambda u: ("title", ""))
    assert picked[0]["fetch_status"] == "summary" and picked[0]["text"]
