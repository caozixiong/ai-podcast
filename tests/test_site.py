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
