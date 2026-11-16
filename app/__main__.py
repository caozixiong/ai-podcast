"""CLI 入口：python -m app <doctor | preview | draft [--all] | publish [--push] | republish <episode_id> [--push]>"""
from __future__ import annotations

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def doctor(root: str) -> int:
    print("== AI Podcast doctor（只读诊断，不打印任何密钥值）==")
    required_ok = True
    for t in ("python", "git", "ffmpeg", "ffprobe", "rclone", "gh"):
        p = shutil.which(t)
        print(f"  {t:12} {'OK ' + p if p else '缺失'}")
        if t in ("git", "ffmpeg", "ffprobe") and not p:
            required_ok = False
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    print(f"  GEMINI_API_KEY  {'已设置 (len=%d)' % len(key) if key else '未设置'}")
    try:
        with open(os.path.join(root, "config", "show.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        print(f"  config/show.json  OK (llm.model={cfg.get('llm', {}).get('model')})")
    except Exception as ex:
        print("  config 读取失败:", ex)
        required_ok = False
    try:
        from app.state import EpisodeStore
        st = EpisodeStore(os.path.join(root, "data"))
        print(f"  已记录节目数: {len(st.load_episodes())}")
    except Exception as ex:
        print("  状态读取失败:", ex)
    print("  doctor 结果:", "PASS" if required_ok else "PARTIAL")
    return 0


def preview(root: str) -> int:
    from app.site import build_site
    from app.state import EpisodeStore
    st = EpisodeStore(os.path.join(root, "data"))
    eps = [e.to_dict() for e in st.load_episodes()]
    with open(os.path.join(root, "config", "show.json"), encoding="utf-8") as f:
        show = json.load(f)
    docs = os.path.join(root, "docs")
    build_site(eps, docs, show)
    print("preview 已生成：%s（%d 集）" % (docs, len(eps)))
    print("首页：%s" % os.path.join(docs, "index.html"))
    return 0


def main(argv):
    if len(argv) < 1:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "doctor":
        return doctor(ROOT)
    if cmd == "preview":
        return preview(ROOT)
    if cmd == "publish":
        from app.publish import run_publish
        rep = run_publish(ROOT, do_push="--push" in argv)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    if cmd == "republish":
        # python -m app republish <episode_id> [--push]：按原来源用新流程重做某一集
        ids = [a for a in argv[1:] if not a.startswith("--")]
        if not ids:
            print("用法：python -m app republish <episode_id> [--push]")
            return 1
        from app.publish import run_republish
        rep = run_republish(ROOT, ids[0], do_push="--push" in argv)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep.get("result") == "republished" else 2
    if cmd == "draft":
        # 只出草稿：不配音、不建站、不推送、不记已读；结果另存 logs/drafts/（不入库）
        from datetime import datetime
        from app.publish import run_draft
        rep = run_draft(ROOT, ignore_seen="--all" in argv)
        out_dir = os.path.join(ROOT, "logs", "drafts")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, datetime.now().strftime("draft-%Y%m%d-%H%M%S.json"))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        print("草稿已保存：" + path)
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
