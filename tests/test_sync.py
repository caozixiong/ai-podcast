"""两套流程往同一个仓库推（Hermes 写根目录，Gemini 写 gemini/）：推送前必须先接入远端的新提交。

用临时目录里的真实 git 仓库测，不碰 GitHub。
"""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app.publish as pub  # noqa

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert p.returncode == 0, p.stdout.decode(errors="replace")
    return p.stdout.decode(errors="replace").strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


@pytest.fixture
def repos(tmp_path):
    """remote（裸仓库）+ hermes、gemini 两个克隆；初始提交里有 index.html 和 data/seen.json。"""
    remote = str(tmp_path / "remote.git")
    git(str(tmp_path), "init", "--bare", "-b", "main", remote)
    clones = {}
    for name in ("hermes", "gemini"):
        d = str(tmp_path / name)
        git(str(tmp_path), "clone", remote, d)
        git(d, "config", "user.name", name)
        git(d, "config", "user.email", f"{name}@example.invalid")
        git(d, "config", "commit.gpgsign", "false")
        git(d, "config", "core.autocrlf", "false")
        clones[name] = d
    h = clones["hermes"]
    git(h, "checkout", "-b", "main")
    write(os.path.join(h, "index.html"), "hermes home v1\n")
    write(os.path.join(h, "data", "seen.json"), "{}\n")
    git(h, "add", "-A")
    git(h, "commit", "-m", "init")
    git(h, "push", "origin", "HEAD:main")
    g = clones["gemini"]
    git(g, "fetch", "origin")
    git(g, "checkout", "-b", "main", "origin/main")
    return clones


def gemini_publishes(g, day):
    git(g, "pull", "--rebase", "origin", "main")
    write(os.path.join(g, "gemini", "index.html"), f"gemini home {day}\n")
    write(os.path.join(g, "gemini", "episodes", f"gm-{day}.html"), "episode\n")
    git(g, "add", "-A")
    git(g, "commit", "-m", f"gemini episode gm-{day}")
    git(g, "push", "origin", "HEAD:main")


def test_sync_brings_in_gemini_commits_and_keeps_local_work(repos):
    h, g = repos["hermes"], repos["gemini"]
    gemini_publishes(g, "2026-11-20")
    # Hermes：一个还没推送的本地提交 + 一个没提交的状态文件改动（每天发布后都会留下）
    write(os.path.join(h, "episodes", "ep-2026-11-20.html"), "hermes episode\n")
    git(h, "add", "-A")
    git(h, "commit", "-m", "episode ep-2026-11-20")
    write(os.path.join(h, "data", "seen.json"), '{"abc": true}\n')

    ok, note = pub._sync_with_remote(h)
    assert ok, note
    assert os.path.exists(os.path.join(h, "gemini", "index.html"))                    # Gemini 的页面接进来了
    assert os.path.exists(os.path.join(h, "episodes", "ep-2026-11-20.html"))          # 本地提交还在
    assert open(os.path.join(h, "data", "seen.json")).read() == '{"abc": true}\n'      # 没提交的改动原样恢复
    assert "episode ep-2026-11-20" in git(h, "log", "--oneline", "-1")
    assert pub._sync_with_remote(h) == (True, "远端没有新提交")


def test_push_after_the_other_side_pushed_first(repos):
    h, g = repos["hermes"], repos["gemini"]
    gemini_publishes(g, "2026-11-20")
    write(os.path.join(h, "index.html"), "hermes home v2\n")
    git(h, "add", "-A")
    git(h, "commit", "-m", "episode ep-2026-11-20")
    ok, out = pub._push(h)                       # 不先同步的话这里会被拒（non-fast-forward）
    assert ok, out
    # 远端同时有两边的内容；Gemini 再发一期也不受影响
    gemini_publishes(g, "2026-11-21")
    assert open(os.path.join(g, "index.html")).read() == "hermes home v2\n"
    assert os.path.exists(os.path.join(g, "gemini", "episodes", "gm-2026-11-20.html"))
    log = git(g, "log", "--oneline")
    assert "episode ep-2026-11-20" in log and "gemini episode gm-2026-11-21" in log


def test_conflict_is_abandoned_not_forced(repos):
    h, g = repos["hermes"], repos["gemini"]
    # 对方越界改了 Hermes 的首页（提示词里禁止，但要保证这边不会因此强推或丢东西）
    write(os.path.join(g, "index.html"), "overwritten by the other side\n")
    git(g, "add", "-A")
    git(g, "commit", "-m", "out of bounds")
    git(g, "push", "origin", "HEAD:main")
    write(os.path.join(h, "index.html"), "hermes home v2\n")
    git(h, "add", "-A")
    git(h, "commit", "-m", "episode ep-2026-11-20")
    before = git(h, "rev-parse", "HEAD")

    ok, note = pub._sync_with_remote(h)
    assert not ok and "rebase 失败" in note
    assert git(h, "rev-parse", "HEAD") == before                                     # 本地保持原样
    assert open(os.path.join(h, "index.html")).read() == "hermes home v2\n"
    assert git(h, "status", "--porcelain") == ""                                     # 没有留下半截 rebase
    ok, out = pub._push(h)
    assert not ok                                                                    # 推不上去就报错，不强推
    assert "overwritten" in git(g, "show", "origin/main:index.html")
