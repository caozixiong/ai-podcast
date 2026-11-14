import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.state import EpisodeStore, Episode  # noqa


def test_upsert_get_and_today(tmp_path):
    st = EpisodeStore(str(tmp_path / "data"))
    ep = Episode(episode_id="ep-1", title="A", date="2026-01-01", items=[],
                 script="hello, enough text to pass length checks", created_at="2026-01-01T08:30:00")
    st.upsert(ep)
    got = st.get("ep-1")
    assert got and got.title == "A"
    assert st.has_published_today("2026-01-01") is False      # status=collected，未发布
    ep.status = "pushed"
    st.upsert(ep)
    assert st.has_published_today("2026-01-01") is True
    assert st.has_published_today("2026-01-02") is False


def test_seen_dedup(tmp_path):
    st = EpisodeStore(str(tmp_path / "data"))
    assert st.is_seen("k1") is False
    st.mark_seen(["k1", "k2"])
    assert st.is_seen("k1") is True and st.is_seen("k2") is True


def test_corrupt_recovery(tmp_path):
    st = EpisodeStore(str(tmp_path / "data"))
    os.makedirs(st.state_dir, exist_ok=True)
    with open(st.episodes_path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    assert st.load_episodes() == []
    assert os.path.exists(st.episodes_path + ".corrupt")      # 损坏被保留备份
