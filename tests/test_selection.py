"""选题逻辑（纯离线）：关键词整词匹配、按主题打分、相关度下限、重复新闻、arXiv 限速。

起因：ep-2026-11-19 六条素材没有一条讲 X 射线 / CT / AI 图像处理。头条是噬菌体自组装论文，
它的"相关度 1"来自关键词 SEM 按子串命中了 self-as·sem·bly。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app.collect as c  # noqa
import app.publish as pub  # noqa
from app.collect import RelevanceScorer, _kw_pattern, _strip_seo_tail, arxiv_url, title_key  # noqa
from app.enrich import drop_repeats, select_items  # noqa
from app.state import Episode, EpisodeStore  # noqa


def _real_cfg():
    with open(os.path.join(ROOT, "config", "sources.json"), encoding="utf-8") as f:
        sources = json.load(f)
    with open(os.path.join(ROOT, "config", "show.json"), encoding="utf-8") as f:
        show = json.load(f)
    return sources, show


# ---------- 关键词匹配 ----------

def test_latin_keywords_match_whole_words_only():
    sem = _kw_pattern("SEM")
    assert not sem.search("Electrostatic interactions drive the self-assembly of coat proteins")
    assert not sem.search("we assemble combined methods; semiconductor ensemble")
    assert sem.search("characterized by SEM and TEM")
    assert sem.search("FIB-SEM tomography")
    assert not sem.search("a sem-automatic pipeline")          # 全大写缩写区分大小写


def test_acronym_next_to_chinese_and_plural():
    assert _kw_pattern("TGV").search("华汉伟业携量产级TGV全工艺流程AOI检测方案")
    assert _kw_pattern("TSV").search("copper-filled TSVs after annealing")
    assert not _kw_pattern("CT").search("CTPA scans of pulmonary emboli")
    assert _kw_pattern("CT").search("micro-CT of battery electrodes")


def test_space_and_hyphen_are_optional():
    p = _kw_pattern("non-destructive")
    for text in ("Nondestructive 3D metrology", "non-destructive testing", "non destructive evaluation"):
        assert p.search(text)
    assert _kw_pattern("X-ray").search("an X ray source") and _kw_pattern("X-ray").search("soft x-rays")
    assert _kw_pattern("3D 堆叠").search("3D堆叠封装")


# ---------- 按主题打分 ----------

TOPICS = {
    "topics": [
        {"name": "CT", "weight": 3, "core": True, "keywords": ["X-ray", "computed tomography"]},
        {"name": "AI", "weight": 2, "core": False, "keywords": ["segmentation", "deep learning"]},
    ],
    "exclude_title_keywords": ["市场规模"],
}


def test_title_hit_counts_double_and_each_topic_once():
    s = RelevanceScorer(TOPICS)
    assert s.score("X-ray computed tomography of welds", "X-ray X-ray")[0] == 6      # 标题命中：3×2，只计一次
    assert s.score("Porosity in castings", "measured by X-ray computed tomography")[0] == 3
    score, hits = s.score("Deep learning segmentation of X-ray images", "")
    assert score == 6 + 4 and hits == ["CT@标题:X-ray", "AI@标题:segmentation"]


def test_caret_keyword_counts_in_title_only():
    cfg = {"topics": [{"name": "CT", "weight": 3, "core": True, "keywords": ["^X-ray", "X-ray imaging"]},
                      {"name": "AI", "weight": 2, "core": False, "keywords": ["reconstruction"]}]}
    s = RelevanceScorer(cfg)
    # X 射线衍射做表征的凝聚态论文：摘要里有 X-ray，标题里有 reconstruction（晶格重构），不该算切题
    assert s.score("Al-Sublattice Reconstruction in Sm2Ir8Al31", "single-crystal X-ray diffraction")[0] == 0
    assert s.score("X-Ray Baggage Segmentation", "")[0] == 6
    assert s.score("Detector gap interpolation", "photon-counting X-ray imaging")[0] == 3


def test_no_core_topic_means_zero():
    s = RelevanceScorer(TOPICS)
    score, hits = s.score("Deep learning segmentation of street scenes", "segmentation everywhere")
    assert score == 0 and hits                                   # 只沾边：记了命中，但不给分


def test_title_exclusion():
    assert RelevanceScorer(TOPICS).score("工业CT市场规模预测 X-ray", "")[0] == 0


MED = {
    "topics": [
        {"name": "CT", "weight": 3, "core": True, "keywords": ["X-ray", "CT", "computed tomography"]},
        {"name": "AI", "weight": 2, "core": False, "keywords": ["segmentation", "reconstruction"]},
        {"name": "工业", "weight": 3, "core": False, "industrial": True, "keywords": ["weld", "industrial"]},
    ],
    "medical_exclusion": {"penalty": 5, "strong_keywords": ["patient", "pelvic", "low-dose CT"],
                          "weak_keywords": ["medical", "clinical"]},
}


def test_medical_imaging_is_excluded_not_just_downranked():
    s = RelevanceScorer(MED)
    # 标题里有医学词（强词、泛词都算）→ 排除
    assert s.score("Low-Dose CT Denoising", "")[0] == 0
    score, hits = s.score("Medical CT segmentation", "")
    assert score == 0 and hits == ["排除(医学影像):medical"]
    # 摘要里出现一个解剖/病患类的强词 → 排除（如只在摘要里写了 pelvic 的锥束 CT 重建论文）
    assert s.score("Cone-Beam CT Reconstruction with Learned Warm Starts", "evaluated on pelvic scans")[0] == 0
    # 摘要里两个泛词 → 排除
    assert s.score("3D CT segmentation", "a medical foundation model for clinical use")[0] == 0


def test_medical_mention_in_passing_only_costs_points():
    s = RelevanceScorer(MED)
    # 工业论文（标题命中 industrial 主题）顺带提一句患者成像：不排除，只扣分
    score, hits = s.score("X-ray CT inspection of welds", "unlike patient imaging, dose is not a concern")
    assert score == 6 + 6 - 5 and hits[-1] == "降权(提到医学):patient"
    # 通用方法论文摘要里只有一个泛词：不排除，只扣分
    assert s.score("Sparse-view CT reconstruction", "applications range from medical to materials")[0] == 6 + 4 - 5
    # 但工业标题救不了"摘要里一堆医学词"
    assert s.score("Industrial CT segmentation", "validated on patient pelvic scans in clinical practice")[0] == 0
    # 没有医学词：不受影响
    assert s.score("X-ray CT inspection of welds", "porosity analysis")[0] == 12


def test_flat_priority_keywords_still_work():
    s = RelevanceScorer({}, ["TSV", "封装"])
    assert s.score("TSV 检测新进展", "先进封装")[0] > s.score("无关新闻", "天气")[0] == 0


# ---------- 用真实配置核对 11-19 那一期 ----------

OFF_TOPIC = [
    ("Electrostatic interactions drive the self-assembly of secondary coat-protein layers around wild-type MS2 particles",
     "MS2 coat protein dimers can assemble into additional capsid layers. We characterize the particles through "
     "transmission electron microscopy, gel electrophoresis, and dynamic light scattering."),
    ("Shedding Light on Quantum Non-Classicality: Witnessing Wigner Negativity with Free Electrons",
     "phase-resolved quantum photon-induced near-field electron microscopy (QPINEM); full optical reconstruction."),
    ("Unmyelinated axons alter the biological interpretation of MRI biomarkers of demyelination",
     "combining diffusion MRI and electron microscopy (EM) in the cuprizone mouse model; automated tools for "
     "instance-level EM segmentation of white matter."),
    ("Micrograph B-factor estimation via Wilson statistics",
     "The single-particle cryo-electron microscopy imaging process; corrected as a post-processing stage of the "
     "3D reconstruction. We show such an approach can improve reconstruction resolution."),
    ("PAISF: A Partially Adaptive In-Order Selection Function for Routing in On-Chip, Inter-Chiplet Networks",
     "deadlock-free routing for chiplet-based systems built on a wafer-scale interposer."),
    ("Accelerating Variational Cone-Beam CT Reconstruction with Learned Warm Starts",
     "image-guided radiotherapy; evaluated on pelvic cone-beam CT data."),
    ("RouteDiff: Low-Dose CT Denoising via Fidelity-Refinement Routing and Hypergraph State Regulation",
     "low-dose CT (LDCT) denoising with a diffusion model."),
    ("NativeSeg: Native Vision Language Segmentation of 3D Computed Tomography via Spatial Visual Tokens",
     "a medical vision-language model for multi-organ segmentation."),
    ("A 3D foundation model unifying CT interpretation and segmentation",
     "trained on clinical CT volumes with radiologist reports from several hospitals."),
    ("Anisotropic Structural and Magnetic Response in Sm2Ir8Al31: Al-Sublattice Reconstruction",
     "single-crystal X-ray diffraction and magnetization measurements."),
    ("2026武汉黄金回收规范化指南：无损检测、全程留痕、票据留存", ""),
    ("X 射线检测系统市场增长 |全球报告 [2034]", ""),
]

ON_TOPIC = [
    ("Nondestructive 3D Metrology of Through-Glass Via Geometry and Internal Wall Texture Using X-ray Microscopy",
     "through-glass vias for semiconductor packaging."),
    ("Task-driven sparse-view planar X-ray computed tomography for axial metrology",
     "reconstruction of PCB layers from few projections."),
    ("The Path to NDE 4.0: A Review of Algorithms, Techniques, and Strategies for Autonomous Inspection",
     "deep learning for industrial inspection."),
    ("华汉伟业携量产级TGV全工艺流程AOI检测方案亮相第二十四届封测年会", ""),
    ("【明日主题前瞻】总投资50亿元，又一先进封装项目开工", ""),
    ("Deep learning segmentation of FIB-SEM volumes of battery cathodes", ""),
    ("X-ray computed tomography of porosity in additively manufactured alloys",
     "Unlike medical CT, industrial CT of dense metal parts suffers from beam hardening."),
]


def test_real_config_rejects_ep_2026_11_19_and_keeps_core_topics():
    sources, show = _real_cfg()
    floor = show["episode"]["min_relevance"]
    s = RelevanceScorer(sources)
    for title, abstract in OFF_TOPIC:
        score, hits = s.score(title, abstract)
        assert score < floor, (title, score, hits)
    for title, abstract in ON_TOPIC:
        score, hits = s.score(title, abstract)
        assert score >= floor, (title, score, hits)


def test_real_config_sources_are_well_formed():
    sources, _ = _real_cfg()
    arxiv = [x for x in sources["sources"] if x["type"] == "arxiv"]
    assert arxiv and all(arxiv_url(x).startswith("https://export.arxiv.org/api/query?") for x in arxiv)
    # 泛泛的 "electron microscopy" 不能再单独作为检索条件（它把各学科的电镜论文都捞进来）
    for x in arxiv:
        q = x.get("query", "")
        if "electron microscopy" in q:
            assert " AND " in q, x["name"]
    assert any(t.get("core") for t in sources["topics"])


# ---------- 标题清洗与重复新闻 ----------

def test_seo_tail_stripped_and_title_key():
    assert _strip_seo_tail("华汉伟业携量产级TGV全工艺流程AOI检测方案亮相第二十四届封测年会_华汉伟业_半导体") == \
        "华汉伟业携量产级TGV全工艺流程AOI检测方案亮相第二十四届封测年会"
    assert _strip_seo_tail("短标题_栏目") == "短标题_栏目"            # 剩下太短就不动，避免误伤
    assert title_key("【明日主题前瞻】总投资50亿元，又一先进封装项目开工") == \
        title_key("【今日主题前瞻】总投资50亿元，又一先进封装项目开工_市场测评_市场_中金在线")


def test_drop_repeats_same_story_different_publisher():
    past = ["【明日主题前瞻】总投资50亿元，又一先进封装项目开工"]
    items = [
        {"id": "1", "title": "【今日主题前瞻】总投资50亿元，又一先进封装项目开工_市场测评_市场_中金在线"},
        {"id": "2", "title": "东山精密港股敲钟，战略合作俐玛精测，以工业CT切入AI供应链增长新空间"},
        {"id": "3", "title": "企业间合作 以工业CT切入AI供应链增长新空间"},
        {"id": "4", "title": "Accelerating Variational Cone-Beam CT Reconstruction with Learned Warm Starts"},
        {"id": "5", "title": "Taming Neural Fields with a Frequency-Lipschitz Curriculum for Sparse-View CT Reconstruction"},
    ]
    kept, dropped = drop_repeats(items, past)
    assert [i["id"] for i in kept] == ["2", "4", "5"]              # 两篇不同的 CT 重建论文都保留
    assert [i["id"] for i in dropped] == ["1", "3"]


def test_select_items_respects_min_relevance():
    items = [{"id": "a", "title": "切题", "url": "https://arxiv.org/abs/1", "fetch_status": "abstract",
              "summary": "x" * 300, "relevance": 9},
             {"id": "b", "title": "沾边", "url": "https://arxiv.org/abs/2", "fetch_status": "abstract",
              "summary": "x" * 300, "relevance": 3},
             {"id": "c", "title": "无关", "url": "https://arxiv.org/abs/3", "fetch_status": "abstract",
              "summary": "x" * 300, "relevance": 0}]
    assert [i["id"] for i in select_items(items, min_relevance=4)] == ["a"]
    assert [i["id"] for i in select_items(items)] == ["a", "b", "c"]   # 不设下限时保持旧行为（republish 用）


# ---------- 采集：arXiv 限速、新闻时间窗 ----------

class FakeFeed:
    bozo = False

    def __init__(self, entries):
        self.entries = entries

    def get(self, k, d=None):
        return getattr(self, k, d)


def _fmt(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def test_arxiv_query_url_and_pause_between_requests(monkeypatch):
    url = arxiv_url({"query": 'all:"X-ray inspection" AND (cat:cs.CV OR cat:eess.IV)', "max_results": 12})
    assert "search_query=all:%22X-ray%20inspection%22%20AND%20(cat:cs.CV%20OR%20cat:eess.IV)" in url
    assert "max_results=12" in url and "sortBy=submittedDate" in url

    calls, sleeps = [], []

    class R:
        content = b""

        def raise_for_status(self):
            pass

    monkeypatch.setattr(c.requests, "get", lambda u, **k: calls.append(u) or R())
    monkeypatch.setattr(c.feedparser, "parse", lambda data: FakeFeed([]))
    monkeypatch.setattr(c.time, "sleep", lambda s: sleeps.append(s))
    cfg = {"sources": [{"name": "A1", "type": "arxiv", "query": "all:TSV"},
                       {"name": "A2", "type": "arxiv", "query": "all:TGV"}]}
    c.collect_sources(cfg, lookback_days=7)
    assert len(calls) == 2
    assert len(sleeps) == 1 and 2.5 < sleeps[0] <= c.ARXIV_MIN_INTERVAL     # 第二次请求前等够 3 秒


def test_arxiv_retries_once_then_reports(monkeypatch):
    attempts = []

    def boom(u, **k):
        attempts.append(u)
        raise c.requests.ConnectionError("down")

    monkeypatch.setattr(c.requests, "get", boom)
    monkeypatch.setattr(c.time, "sleep", lambda s: None)
    items = c.collect_sources({"sources": [{"name": "A1", "type": "arxiv", "query": "all:TSV"}]})
    assert len(attempts) == 2
    assert items[-1]["id"] == "_errors" and "A1" in items[-1]["summary"]


def test_news_window_shorter_than_paper_window(monkeypatch):
    now = datetime.now(timezone.utc)
    old = _fmt(now - timedelta(days=20))
    monkeypatch.setattr(c.feedparser, "parse",
                        lambda url: FakeFeed([{"title": "三周前的 TSV 新闻", "link": "https://n.com/old", "published": old}]))
    cfg = {"sources": [{"name": "S", "type": "rss", "url": "http://x/rss"}]}
    assert c.collect_sources(cfg, lookback_days=30, priority_keywords=["TSV"])                 # 不单设时沿用总时间窗
    assert not c.collect_sources(cfg, lookback_days=30, priority_keywords=["TSV"], news_lookback_days=10)


# ---------- 候选池：先去掉播过的，再挑 ----------

def test_gather_candidates_filters_seen_before_picking(monkeypatch, tmp_path):
    """以前先把候选截到 40 条再去掉播过的：排在前面的都播过以后，后面的新素材永远轮不到。"""
    pool = [{"id": f"s{i}", "title": f"已播论文 {i} 关于X射线断层成像方法研究", "url": f"https://arxiv.org/abs/{i}",
             "kind": "arxiv", "fetch_status": "abstract", "summary": "x" * 300, "relevance": 12}
            for i in range(60)]
    pool += [{"id": "new1", "title": "全新的工业CT缺陷检测论文", "url": "https://arxiv.org/abs/n1", "kind": "arxiv",
              "fetch_status": "abstract", "summary": "x" * 300, "relevance": 8},
             {"id": "weak", "title": "只沾一点边的论文", "url": "https://arxiv.org/abs/n2", "kind": "arxiv",
              "fetch_status": "abstract", "summary": "x" * 300, "relevance": 3},
             {"id": "_errors", "title": "采集警告", "url": "", "summary": "A1: timeout", "relevance": -1}]
    monkeypatch.setattr(pub, "collect_sources", lambda *a, **k: list(pool))
    store = EpisodeStore(str(tmp_path))
    store.mark_seen([f"s{i}" for i in range(60)])
    store.upsert(Episode(episode_id="ep-x", title="t", date="2026-11-18", script="s",
                         items=[{"id": "p", "title": "往期播过的标题：玻璃基板进入大客户认证阶段"}]))
    show = {"episode": {"min_relevance": 4, "lookback_days": 30}}
    got = pub.gather_candidates(show, {}, store)
    assert [i["id"] for i in got["eligible"]] == ["new1"]
    assert got["warnings"] == ["A1: timeout"] and len(got["fresh"]) == 2
    # --all 试跑：播过的也参与（60 条示例标题只差一个编号，会被当成同一条而只留一条）
    again = {i["id"] for i in pub.gather_candidates(show, {}, store, ignore_seen=True)["eligible"]}
    assert again == {"s0", "new1"}
