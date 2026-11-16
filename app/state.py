"""持久、原子化的节目状态与去重。

状态文件（均为公开内容，可安全入库）：
  data/episodes.json  — 已发布/进行中节目记录
  data/seen.json      — 去重：已播报过的来源 id / url

写入是原子的：先写同目录临时文件再 os.replace，
中途崩溃也不会损坏状态。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, asdict, field
from typing import Any

STATE_DIR_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _atomic_write_json(path: str, obj: Any) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        # 状态损坏：保留备份，返回默认以便恢复
        try:
            os.replace(path, path + ".corrupt")
        except OSError:
            pass
        return default


@dataclass
class Episode:
    episode_id: str
    title: str
    date: str                       # YYYY-MM-DD（按 America/Toronto）
    items: list                     # 本集来源列表
    script: str                     # 口播逐字稿
    audio: dict = field(default_factory=dict)   # {drive_id, share_url, duration_sec, size_bytes, local_path}
    status: str = "collected"       # collected->scripted->audio_ready->uploaded->site_built->pushed->verified
    history: list = field(default_factory=list)
    quality: dict = field(default_factory=dict)  # 事实卡/自检记录（新流程才有）
    commit_sha: str = ""
    pages_url: str = ""
    created_at: str = ""
    published_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Episode":
        keys = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in keys})


class EpisodeStore:
    def __init__(self, state_dir: str = STATE_DIR_DEFAULT):
        self.state_dir = state_dir
        self.episodes_path = os.path.join(state_dir, "episodes.json")
        self.seen_path = os.path.join(state_dir, "seen.json")

    # ---- episodes ----
    def load_episodes(self) -> list[Episode]:
        raw = _read_json(self.episodes_path, [])
        return [Episode.from_dict(d) for d in raw if isinstance(d, dict)]

    def save_episodes(self, eps: list[Episode]) -> None:
        _atomic_write_json(self.episodes_path, [e.to_dict() for e in eps])

    def get(self, episode_id: str):
        for e in self.load_episodes():
            if e.episode_id == episode_id:
                return e
        return None

    def upsert(self, ep: Episode) -> None:
        eps = self.load_episodes()
        replaced = False
        for i, e in enumerate(eps):
            if e.episode_id == ep.episode_id:
                eps[i] = ep
                replaced = True
                break
        if not replaced:
            eps.append(ep)
        eps.sort(key=lambda x: (x.created_at or x.date), reverse=True)
        self.save_episodes(eps)

    def has_published_today(self, date_str: str) -> bool:
        return any(e.date == date_str and e.status in ("pushed", "verified", "uploaded", "site_built")
                   for e in self.load_episodes())

    # ---- dedup (seen) ----
    def load_seen(self) -> dict:
        return _read_json(self.seen_path, {})

    def save_seen(self, seen: dict) -> None:
        _atomic_write_json(self.seen_path, seen)

    def is_seen(self, key: str) -> bool:
        return key in self.load_seen()

    def mark_seen(self, keys: list[str]) -> None:
        seen = self.load_seen()
        for k in keys:
            seen[k] = True
        self.save_seen(seen)
