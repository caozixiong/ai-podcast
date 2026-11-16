"""事实卡 + 程序自检（纯离线，用假 LLM）。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.editorial import (build_fact_card, check_script, render_cards,  # noqa
                           unsupported_numbers, write_episode, _norm)

FEED = "Google News RSS: TSV / 封装 / 检测 (中文)"

ARXIV = {
    "title": "SPLIT-MOR: Fast and Accurate Model Order Reduction",
    "url": "https://arxiv.org/abs/2611.17693v1", "kind": "arxiv", "publisher": "arXiv",
    "authors": ["Zebin Shi", "Tianxiang Zhu"], "published": "2026-11-13", "relevance": 2,
    "fetch_status": "abstract",
    "text": ("We propose SPLIT-MOR (Subdomain-wise Piecewise Linearization with Interval Tensors), "
             "a nonlinear MOR method that requires no additional training set. SPLIT-MOR achieves up to "
             "an 830x speedup over COMSOL. The relative average error remains below 0.11%."),
}
NEWS_TITLE_ONLY = {
    "title": "先进封装将热与接触难题提前至晶圆测试", "url": "https://news.google.com/rss/articles/X",
    "kind": "news", "publisher": "DIGITIMES", "published": "2026-11-16", "relevance": 1,
    "fetch_status": "title", "text": "",
}

GOOD_SCRIPT = ("大家好，这里是《X射线无损检测与AI图像处理前沿》。\n\n"
               "arXiv 上一篇预印本论文提出了 SPLIT-MOR，在线求解比 COMSOL 最高快 830 倍，平均误差低于 0.11%。"
               "预印本还没有经过同行评审。\n\n"
               "另一条来自 DIGITIMES，标题说先进封装把热与接触难题提前到了晶圆测试，全文暂未获取，细节不展开。\n\n"
               "我们下期见。")


class ScriptedLLM:
    """按调用顺序返回预设内容；记录每次调用参数。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, user, **kw):
        self.calls.append({"system": system, "user": user, **kw})
        if not self.replies:
            raise AssertionError("LLM 被多调用了一次")
        return self.replies.pop(0)


def _card_json(**over):
    card = {"core_claim": "SPLIT-MOR 是无需训练集的非线性模型降阶方法",
            "key_facts": ["无需额外训练集", "平均误差低于 0.11%"],
            "numbers": ["在线求解比 COMSOL 最高快 830 倍", "比 MDEIM 快 95 倍"],   # 95 是编的
            "method_type": "数值模型降阶", "is_ai": False, "maturity": "仿真",
            "caveats": [], "terms": ["model order reduction = 模型降阶"]}
    card.update(over)
    return json.dumps(card, ensure_ascii=False)


def test_unsupported_numbers_ignores_names_and_dims():
    corpus = _norm("speedup of 830x, error below 0.11%, NMC811 cells, 1,500 units")
    assert unsupported_numbers("快 830 倍，误差 0.11%，共 1500 台", corpus) == []
    assert unsupported_numbers("3D 和 2.5D 封装，NMC811", corpus) == []      # 与字母相连的不核对
    assert unsupported_numbers("市场增长 95%", corpus) == ["95"]


def test_range_check_catches_truncated_endpoint():
    from app.editorial import unsupported_ranges
    corpus = _norm("架构师估计只有200到300人；交期从3到6个月延长到6至8个月；反射率 11-15%")
    assert unsupported_ranges("只有200到30人", corpus) == ["200 到 30"]
    assert unsupported_ranges("只有200到300人，交期6至8个月，反射率 11% 到 15%", corpus) == []
    assert unsupported_ranges("2026-2032 年", _norm("2026-2032 年复合增速")) == []


def test_fact_card_drops_invented_numbers():
    llm = ScriptedLLM([_card_json()])
    card = build_fact_card(ARXIV, llm)
    assert llm.calls[0]["json_mode"] is True
    assert "比 MDEIM 快 95 倍" in card["dropped"]
    assert "在线求解比 COMSOL 最高快 830 倍" in card["numbers"]
    assert card["is_ai"] is False


def test_title_only_item_skips_llm():
    llm = ScriptedLLM([])
    card = build_fact_card(NEWS_TITLE_ONLY, llm)
    assert card["core_claim"] == NEWS_TITLE_ONLY["title"] and not llm.calls


def test_render_cards_uses_publisher_not_feed_name():
    it = dict(NEWS_TITLE_ONLY, source_name=FEED)
    text = render_cards([dict(ARXIV, fact_card=build_fact_card(ARXIV, ScriptedLLM([_card_json()]))), it])
    assert "arXiv 预印本（未经同行评审）" in text
    assert "DIGITIMES" in text and "仅标题" in text
    assert FEED not in text


def test_check_script_flags_known_failure_modes():
    bad = ("大家好。据 Google News RSS: TSV / 封装 / 检测 (中文) 的报道，市场增长 95%。"
           "目前行业普遍认为这很重要，虽然摘要没写全。SPLIT-MOR 堪称完美。我们下期见。")
    problems = " | ".join(check_script(bad, [ARXIV, NEWS_TITLE_ONLY], [FEED]))
    assert "RSS 频道名" in problems
    assert "行业普遍认为" in problems
    assert "摘要没写全" in problems
    assert "95" in problems
    assert "完美" in problems
    assert "预印本" in problems
    assert check_script(GOOD_SCRIPT, [ARXIV, NEWS_TITLE_ONLY], [FEED]) == []


def test_check_script_flags_tts_unfriendly_text():
    latex = GOOD_SCRIPT.replace("我们下期见。", "变化为 $O(\\eta^2)$。我们下期见。")
    assert any("LaTeX" in p for p in check_script(latex, [ARXIV], [FEED]))
    caret = GOOD_SCRIPT.replace("我们下期见。", "变化量为 O(η^2)。我们下期见。")
    assert any("LaTeX" in p for p in check_script(caret, [ARXIV], [FEED]))
    with_title = GOOD_SCRIPT.replace("提出了 SPLIT-MOR", f"《{ARXIV['title']}》提出了 SPLIT-MOR")
    assert any("英文论文原标题" in p for p in check_script(with_title, [ARXIV], [FEED]))


def test_write_episode_repairs_once_then_passes():
    bad = GOOD_SCRIPT.replace("预印本还没有经过同行评审。", "据公开报道，这非常重要。") + '\n{"title":"热仿真"}'
    good = GOOD_SCRIPT + '\n{"title":"热仿真提速 830 倍"}'
    llm = ScriptedLLM([_card_json(), bad, good])
    ep = write_episode([ARXIV, NEWS_TITLE_ONLY], llm, feed_names=[FEED])
    q = ep["quality"]
    assert q["violations_first"] and q["repaired"] and q["violations_final"] == []
    assert ep["title"] == "热仿真提速 830 倍"
    assert len(llm.calls) == 3                              # 1 张事实卡 + 撰稿 + 改写
    assert FEED not in llm.calls[1]["user"]                 # 撰稿时不再给频道名


def test_write_episode_blocks_when_still_failing():
    bad = GOOD_SCRIPT.replace("830 倍", "8300 倍")
    llm = ScriptedLLM([_card_json(), bad, bad])
    try:
        write_episode([ARXIV, NEWS_TITLE_ONLY], llm, feed_names=[FEED])
        raise AssertionError("自检不通过应拒绝发布")
    except Exception as e:
        assert "拒绝发布" in str(e) and "8300" in str(e)


def test_write_episode_warn_mode_returns_problems():
    bad = GOOD_SCRIPT.replace("830 倍", "8300 倍")
    llm = ScriptedLLM([_card_json(), bad, bad])
    ep = write_episode([ARXIV, NEWS_TITLE_ONLY], llm, feed_names=[FEED], on_violation="warn")
    assert any("8300" in p for p in ep["quality"]["violations_final"])


def test_write_episode_adds_missing_frame():
    body = "arXiv 上一篇预印本论文提出了 SPLIT-MOR，在线求解比 COMSOL 最高快 830 倍，平均误差低于 0.11%。"
    llm = ScriptedLLM([_card_json(), body + '\n{"title":"t"}'])
    ep = write_episode([ARXIV], llm, show_name="测试台")
    assert ep["script"].startswith("大家好，这里是《测试台》。")
    assert ep["script"].rstrip().endswith("我们下期见。")
