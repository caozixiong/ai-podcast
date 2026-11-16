#!/usr/bin/env python
"""每日定时发布（no_agent cron 用）。

在 C:\\Projects\\ai-podcast 运行 `python -m app publish --push`，
把结果归并成一行人话输出（交付到聊天）。不打印任何令牌。
"""
import subprocess
import os
import json

ROOT = r"C:\Projects\ai-podcast"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
SITE = "https://caozixiong.github.io/ai-podcast/"

env = dict(os.environ)
env.update({"GCM_INTERACTIVE": "Never", "GIT_TERMINAL_PROMPT": "0"})

try:
    p = subprocess.run(
        [PY, "-m", "app", "publish", "--push"],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800)  # 事实卡+撰稿+自检需多次调用本地模型
    out = p.stdout.decode(errors="replace")
    rep = {}
    i = out.find("{")
    if i >= 0:
        try:
            rep = json.loads(out[i:])
        except Exception:
            rep = {}
    result = rep.get("result") or rep.get("status") or ""
    stages = rep.get("stages", {})
    if result in ("ok", "pushed", "site_built"):
        print("✅ 《%s》已发布" % rep.get("title", "?"))
        print("   音频: %s" % stages.get("tts", "?"))
        print("   链接: %sepisodes/%s.html" % (SITE, rep.get("episode_id", "?")))
    elif result == "skipped":
        print("⏭️ 今日跳过：%s" % rep.get("reason", "素材不足"))
    else:
        print("⚠️ 发布状态=%s" % (result or "unknown"))
        if rep.get("reason"):
            print("   原因：%s" % rep["reason"])
        print(out[-800:])
except Exception as e:
    print("❌ 发布异常：%s: %s" % (type(e).__name__, e))
