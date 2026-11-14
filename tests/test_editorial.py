import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.editorial import _parse_output, write_episode  # noqa


def test_parse_output_strips_markdown_and_title():
    raw = (
        "### 开场\n"
        "大家好，这里是《X射线无损检测与AI图像处理前沿》。今天我们聊 TSV 为什么重要。\n"
        "**加粗** 也不该出现在正文里。\n"
        '{"title":"TSV 前沿速览"}'
    )
    body, title = _parse_output(raw, fallback_title="fallback")
    assert title == "TSV 前沿速览"
    assert "TSV 前沿速览" not in body            # JSON 标题行被剥离
    assert not body.lstrip().startswith("###")   # markdown 标题被清理
    assert "大家好" in body


def test_write_episode_rejects_empty(monkeypatch):
    class FakeLLM:
        def complete(self, *a, **k):
            return "   "                          # 空/过短
    try:
        write_episode([{"title": "x", "url": "https://a", "relevance": 1}], FakeLLM())
        raise AssertionError("应拒绝空稿")
    except Exception as e:
        assert "拒绝发布" in str(e)


def test_write_episode_no_items(monkeypatch):
    class FakeLLM:
        def complete(self, *a, **k):
            raise AssertionError("无素材时不该调用 LLM")
    try:
        write_episode([{"id": "_errors", "title": "警告", "url": "", "relevance": -1}], FakeLLM())
        raise AssertionError("无素材应直接跳过")
    except Exception as e:
        assert "没有可用素材" in str(e)
