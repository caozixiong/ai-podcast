import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.site import build_site  # noqa

CFG = {"show_name": "X射线无损检测与AI图像处理前沿", "subtitle": "测试", "base_path": "/ai-podcast/"}


def test_empty_state(tmp_path):
    out = str(tmp_path / "docs")
    build_site([], out, CFG)
    idx = open(os.path.join(out, "index.html"), encoding="utf-8").read()
    assert "X射线无损检测与AI图像处理前沿" in idx
    assert ("筹备中" in idx) or ("还没有" in idx)          # 友好空状态
    assert os.path.exists(os.path.join(out, "assets", "style.css"))
    assert os.path.exists(os.path.join(out, ".nojekyll"))
    assert 'href="/assets/' not in idx                       # 子路径安全：无绝对 /assets/
    assert 'href="assets/style.css"' in idx


def test_populated_episode(tmp_path):
    ep = {
        "episode_id": "ep-2026-01-01", "title": "TSV 测试集", "date": "2026-01-01",
        "items": [{"title": "TSV 检测", "url": "https://example.com/a",
                   "source_name": "X", "published": "2026-01-01"}],
        "script": "大家好，这里是《X射线无损检测与AI图像处理前沿》。今天我们聊 TSV。我们下期见。",
        "audio": {"preview_url": "https://drive.google.com/file/d/ABC/preview",
                  "share_url": "https://drive.google.com/file/d/ABC/view",
                  "duration_sec": 120},
    }
    out = str(tmp_path / "docs")
    build_site([ep], out, CFG)
    page = open(os.path.join(out, "episodes", "ep-2026-01-01.html"), encoding="utf-8").read()
    assert "iframe" in page
    assert "drive.google.com/file/d/ABC/preview" in page
    assert "drive.google.com/file/d/ABC/view" in page
    assert "example.com/a" in page
    assert "大家好" in page
    assert 'href="../assets/style.css"' in page              # episode 页用 ../ 相对路径
    idx = open(os.path.join(out, "index.html"), encoding="utf-8").read()
    assert "TSV 测试集" in idx


def test_creator_credit_on_index_and_episode_pages(tmp_path):
    cfg = dict(CFG, created_by="Hermes Qwen3.8:27b")
    eps = [
        {"episode_id": "ep-2026-01-03", "title": "老节目", "date": "2026-01-03", "items": [],
         "script": "大家好。", "audio": {}},                                   # 没记创建者 → 用配置里的
        {"episode_id": "ep-2026-01-04", "title": "换模型以后的节目", "date": "2026-01-04", "items": [],
         "script": "大家好。", "audio": {}, "quality": {"created_by": "Hermes Qwen4:32b"}},
    ]
    out = str(tmp_path / "docs")
    build_site(eps, out, cfg)
    old = open(os.path.join(out, "episodes", "ep-2026-01-03.html"), encoding="utf-8").read()
    new = open(os.path.join(out, "episodes", "ep-2026-01-04.html"), encoding="utf-8").read()
    idx = open(os.path.join(out, "index.html"), encoding="utf-8").read()
    assert "本页由 Hermes Qwen3.8:27b 创建" in old
    assert "本页由 Hermes Qwen4:32b 创建" in new and "Qwen3.8" not in new   # 节目里记的优先
    assert "本站节目由 Hermes Qwen3.8:27b 创建" in idx and "Hermes Qwen4:32b 创建" in idx
    assert "对照版本" not in idx                                             # 没配 compare 就不出现链接

    build_site(eps, out, dict(cfg, compare={"label": "对照：Gemini Hydrogen 版", "url": "https://example.com/g/"}))
    idx = open(os.path.join(out, "index.html"), encoding="utf-8").read()
    assert 'href="https://example.com/g/"' in idx and "对照：Gemini Hydrogen 版" in idx


def test_compare_link_waits_for_the_other_site(tmp_path):
    cfg = dict(CFG, compare={"label": "对照：Gemini Hydrogen 版", "url": "gemini/", "only_if_exists": "gemini/index.html"})
    ep = {"episode_id": "ep-1", "title": "t", "date": "2026-01-01", "items": [], "script": "s", "audio": {}}
    out = str(tmp_path / "site")
    build_site([ep], out, cfg)
    assert "Gemini Hydrogen" not in open(os.path.join(out, "index.html"), encoding="utf-8").read()
    os.makedirs(os.path.join(out, "gemini"))
    open(os.path.join(out, "gemini", "index.html"), "w", encoding="utf-8").write("<html>gemini</html>")
    build_site([ep], out, cfg)
    assert 'href="gemini/"' in open(os.path.join(out, "index.html"), encoding="utf-8").read()
    assert 'href="../gemini/"' in open(os.path.join(out, "episodes", "ep-1.html"), encoding="utf-8").read()
    assert open(os.path.join(out, "gemini", "index.html"), encoding="utf-8").read() == "<html>gemini</html>"   # 不碰 gemini/


def test_same_day_cross_links_both_ways(tmp_path):
    """同一个生成器两边用：Hermes 写站点根目录，Gemini 写 gemini/；同一天的两期互相链接。"""
    root = str(tmp_path / "site")
    hermes_cfg = dict(CFG, created_by="Hermes Qwen3.8:27b", compare={
        "label": "对照：Gemini Hydrogen 版", "url": "gemini/", "only_if_exists": "gemini/index.html",
        "episode_pattern": "gemini/episodes/gm-{date}.html", "episode_label": "同一期的 Gemini Hydrogen 版"})
    gemini_cfg = dict(CFG, created_by="Gemini Hydrogen", compare={
        "label": "对照：Hermes Qwen3.8:27b 版", "url": "../",
        "episode_pattern": "../episodes/ep-{date}.html", "episode_label": "同一期的 Hermes Qwen3.8:27b 版"})
    h_eps = [{"episode_id": f"ep-2026-11-{d}", "title": "h", "date": f"2026-11-{d}", "items": [], "script": "s",
              "audio": {}} for d in ("20", "21")]
    g_eps = [{"episode_id": "gm-2026-11-20", "title": "g", "date": "2026-11-20", "items": [], "script": "s",
              "audio": {"duration_sec": 200}}]
    build_site(h_eps, root, hermes_cfg)                              # Hermes 08:30 先发
    build_site(g_eps, os.path.join(root, "gemini"), gemini_cfg)      # Gemini 09:00 后发，只写 gemini/
    build_site(h_eps, root, hermes_cfg)                              # 第二天 Hermes 重建全部页面

    g = open(os.path.join(root, "gemini", "episodes", "gm-2026-11-20.html"), encoding="utf-8").read()
    assert "本页由 Gemini Hydrogen 创建" in g and "Hermes Qwen3.8:27b 创建" not in g
    assert 'href="../../episodes/ep-2026-11-20.html"' in g and "同一期的 Hermes Qwen3.8:27b 版" in g
    assert 'src="../audio/gm-2026-11-20.mp3"' in g                   # 音频在 gemini/audio/ 下
    gi = open(os.path.join(root, "gemini", "index.html"), encoding="utf-8").read()
    assert "本站节目由 Gemini Hydrogen 创建" in gi and 'href="../"' in gi

    h20 = open(os.path.join(root, "episodes", "ep-2026-11-20.html"), encoding="utf-8").read()
    h21 = open(os.path.join(root, "episodes", "ep-2026-11-21.html"), encoding="utf-8").read()
    assert 'href="../gemini/episodes/gm-2026-11-20.html"' in h20 and "同一期的 Gemini Hydrogen 版" in h20
    assert 'href="../gemini/"' in h21 and "同一期" not in h21         # 那天 Gemini 没有节目：只链到它的首页
    for name in ("index.html", os.path.join("episodes", "ep-2026-11-20.html"), os.path.join("audio")):
        assert os.path.exists(os.path.join(root, name))


def test_no_credit_when_not_configured(tmp_path):
    out = str(tmp_path / "docs")
    build_site([{"episode_id": "ep-1", "title": "t", "date": "2026-01-01", "items": [], "script": "s", "audio": {}}],
               out, CFG)
    page = open(os.path.join(out, "episodes", "ep-1.html"), encoding="utf-8").read()
    assert "创建" not in page


def test_sources_show_publisher_and_status(tmp_path):
    ep = {
        "episode_id": "ep-2026-01-02", "title": "新流程", "date": "2026-01-02",
        "items": [
            {"title": "论文", "url": "https://arxiv.org/abs/1", "kind": "arxiv", "publisher": "arXiv",
             "source_name": "arXiv: TSV / 封装", "fetch_status": "abstract", "published": "2026-01-01"},
            {"title": "新闻", "url": "https://news.google.com/x", "kind": "news", "publisher": "DIGITIMES",
             "source_name": "Google News RSS: TSV", "fetch_status": "title", "published": "2026-01-01"},
        ],
        "script": "大家好。我们下期见。", "audio": {}, "quality": {"pipeline": "fact-cards-v1"},
    }
    out = str(tmp_path / "docs")
    build_site([ep], out, CFG)
    page = open(os.path.join(out, "episodes", "ep-2026-01-02.html"), encoding="utf-8").read()
    assert "arXiv 预印本" in page and "DIGITIMES" in page and "（仅标题）" in page
    assert "Google News RSS" not in page
    assert "程序核对" in page
