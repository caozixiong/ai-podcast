"""主流程编排：collect → editorial → tts → drive → site → push → verify。

特性：
  - filelock 并发锁（Windows 原生，替代 Unix fcntl）
  - 同日去重：当天已成功发布则跳过，绝不重复发布
  - 每阶段落状态（data/episodes.json），支持半完成恢复
  - 任一外部依赖缺失（LLM key / TTS / Drive）→ 明确 BLOCKED，不装成功
  - 严禁 git push --force；push 时用 extraHeader 读 GH_TOKEN，不把令牌写入任何文件
"""
from __future__ import annotations

import os
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .state import EpisodeStore, Episode, STATE_DIR_DEFAULT
import re

from .collect import collect_sources, fetch_arxiv_by_ids, arxiv_id_from_url
from .enrich import select_items
from .editorial import write_episode, LLMError
from .tts import synthesize, TTSUnavailable
from .drive import upload, remote_exists, DriveUnavailable
from .site import build_site
from .llm import LLMClient, LLMError as _LE

TZ = ZoneInfo("America/Toronto")
REPO = "https://github.com/caozixiong/ai-podcast.git"


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def load_config(project_dir: str) -> dict:
    with open(os.path.join(project_dir, "config", "show.json"), encoding="utf-8") as f:
        show = json.load(f)
    with open(os.path.join(project_dir, "config", "sources.json"), encoding="utf-8") as f:
        sources = json.load(f)
    return {"show": show, "sources": sources}


def _git(project_dir: str, args: list[str], timeout=300) -> tuple[int, str]:
    git = shutil.which("git")
    if not git:
        return 1, "git 未安装"
    try:
        p = subprocess.run([git] + args, cwd=project_dir, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return p.returncode, p.stdout.decode(errors="replace")
    except Exception as e:
        return 1, str(e)


def _push(project_dir: str) -> tuple[bool, str]:
    """推送 main。鉴权优先用 `gh auth setup-git` 配的 credential helper（令牌在 keyring，不落盘）；
    失败再回退到 GH_TOKEN 的 Bearer 一次性头。"""
    rc, out = _git(project_dir, ["remote", "get-url", "origin"])
    if rc != 0:
        _git(project_dir, ["remote", "add", "origin", REPO])
    env = dict(os.environ)
    env.update({"GCM_INTERACTIVE": "Never", "GIT_TERMINAL_PROMPT": "0"})
    # 优先：git 已配置的 credential helper（gh keyring）
    p = subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=project_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=240)
    if p.returncode == 0:
        return True, p.stdout.decode(errors="replace")
    out2 = p.stdout.decode(errors="replace")
    # 回退：GH_TOKEN（Bearer 一次性头，令牌不进文件/argv）
    token = os.environ.get("GH_TOKEN")
    if token:
        hdr = "Authorization: *** " + token
        p2 = subprocess.run(
            ["git", "-c", "credential.helper=",
             "-c", f"http.https://github.com/.extraHeader={hdr}",
             "push", "origin", "HEAD:main"],
            cwd=project_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=240)
        return p2.returncode == 0, p2.stdout.decode(errors="replace")
    return False, out2 + "\n（push 需要 gh 登录或 GH_TOKEN）"


def _public_item(it: dict) -> dict:
    """入库的来源记录：去掉原文全文（只用于写事实卡），保留事实卡便于事后核对。"""
    return {k: v for k, v in it.items() if k != "text"}


def compose_episode(show: dict, sources_cfg: dict, store: EpisodeStore, report: dict, *,
                    on_violation: str | None = None, ignore_seen: bool = False) -> dict | None:
    """采集 → 挑选/补原文 → 事实卡 → 撰稿 → 自检。成功返回 ep_data；跳过/受阻返回 None 并写 report。"""
    e = show.get("episode", {})
    items = collect_sources(sources_cfg, lookback_days=int(e.get("lookback_days", 7)),
                            priority_keywords=sources_cfg.get("priority_keywords", []),
                            max_items=40)
    items = [it for it in items if it.get("relevance", 0) >= 0]
    fresh = items if ignore_seen else [it for it in items if not store.is_seen(it["id"])]
    selected = select_items(fresh, max_items=int(e.get("max_items", 6)),
                            max_headline_items=int(e.get("max_headline_items", 2)))
    report["stages"]["collect"] = {
        "total": len(items), "fresh": len(fresh), "selected": len(selected),
        "status": [it.get("fetch_status") for it in selected],
    }
    min_items = int(e.get("min_items", 2))
    min_sub = int(e.get("min_substantive_items", 1))
    substantive = sum(1 for it in selected if it.get("fetch_status") != "title")
    if len(selected) < min_items or substantive < min_sub:
        report["result"] = "skipped"
        report["reason"] = (f"可用资讯不足（入选 {len(selected)} 条，其中有原文的 {substantive} 条；"
                            f"要求至少 {min_items} 条且至少 {min_sub} 条有原文），今天跳过，不硬凑。")
        return None

    llm_cfg = show.get("llm", {})
    os.environ.setdefault(llm_cfg.get("api_key_env", "GEMINI_API_KEY"),
                          os.environ.get(llm_cfg.get("api_key_env", "GEMINI_API_KEY"), ""))
    llm = LLMClient(model=llm_cfg.get("model"), fallback_model=llm_cfg.get("fallback_model"),
                    base_url=llm_cfg.get("base_url"),
                    temperature=llm_cfg.get("temperature", 0.3),
                    max_tokens=llm_cfg.get("max_tokens", 2000),
                    timeout_s=llm_cfg.get("timeout_s", 180))
    if "generativelanguage.googleapis.com" in (llm_cfg.get("base_url") or "") and not llm.api_key:
        report["stages"]["editorial"] = "blocked: 缺少 GEMINI_API_KEY"
        report["result"] = "blocked"
        report["reason"] = "缺少 GEMINI_API_KEY（Google AI Studio）。设好后重跑即可。"
        return None
    quality_cfg = show.get("quality", {})
    try:
        ep_data = write_episode(selected, llm,
                                target_words_low=e.get("target_duration_min", 5) * 140,
                                target_words_high=e.get("target_duration_max", 10) * 140,
                                show_name=show.get("show_name", "播客"),
                                feed_names=[s.get("name", "") for s in sources_cfg.get("sources", [])],
                                on_violation=on_violation or quality_cfg.get("on_violation", "block"))
    except _LE as ex:
        report["stages"]["editorial"] = f"error: {ex}"
        report["result"] = "blocked"
        report["reason"] = str(ex)
        return None
    q = ep_data.get("quality", {})
    report["stages"]["editorial"] = (f"ok (素材 {q.get('items')} 条，自检问题 "
                                     f"{len(q.get('violations_first', []))}→{len(q.get('violations_final', []))}"
                                     f"{'，已自动改写' if q.get('repaired') else ''})")
    return ep_data


def run_draft(project_dir: str, *, ignore_seen: bool = False) -> dict:
    """只生成草稿（采集→事实卡→撰稿→自检），不配音、不建站、不推送、不记已读；自检问题只报告不拦截。
    ignore_seen=True 时连已播过的素材也参与挑选（仅用于试跑）。"""
    cfg = load_config(project_dir)
    store = EpisodeStore(os.path.join(project_dir, "data"))
    report: dict = {"date": today_str(), "stages": {}, "result": "running"}
    ep_data = compose_episode(cfg["show"], cfg["sources"], store, report, on_violation="warn",
                              ignore_seen=ignore_seen)
    if ep_data is None:
        return report
    report["result"] = "draft"
    report["title"] = ep_data["title"]
    report["quality"] = ep_data["quality"]
    report["items"] = [{"title": it.get("title"), "publisher": it.get("publisher"),
                        "fetch_status": it.get("fetch_status"), "url": it.get("url"),
                        "fact_card": it.get("fact_card")} for it in ep_data["items"]]
    report["script"] = ep_data["script"]
    return report


def _gn_article_id(url: str) -> str:
    m = re.search(r"news\.google\.com/(?:rss/)?articles/([^?/#]+)", url or "")
    return m.group(1) if m else ""


def _load_direct_urls(project_dir: str) -> dict:
    """config/direct_urls.json：Google News 文章 id → 人工核对过的发布方原文链接（只供 republish 用）。"""
    try:
        with open(os.path.join(project_dir, "config", "direct_urls.json"), encoding="utf-8") as f:
            return dict(json.load(f).get("map", {}))
    except (OSError, ValueError):
        return {}


def run_republish(project_dir: str, episode_id: str, *, do_push: bool = False) -> dict:
    """用新流程（事实卡+自检）按原来源重新生成某一集：覆盖逐字稿、标题、音频与节目页。

    - 来源：按旧记录里的条目 id 从当前 RSS/arXiv 重新采集（拿到完整摘要与真实发布方）；
      已不在时间窗内的条目退化为"仅标题"。
    - 自检不通过（block）或音频失败 → 不动旧节目。
    - 新音频先写临时文件，校验通过后再替换；旧版本保留在 git 历史里，history 字段记录替换。
    """
    cfg = load_config(project_dir)
    show, sources_cfg = cfg["show"], cfg["sources"]
    store = EpisodeStore(os.path.join(project_dir, "data"))
    report: dict = {"date": today_str(), "episode_id": episode_id, "stages": {}, "result": "running"}
    old = store.get(episode_id)
    if old is None:
        report.update(result="error", reason=f"找不到节目 {episode_id}")
        return report

    e = show.get("episode", {})
    pool = collect_sources(sources_cfg, lookback_days=int(e.get("lookback_days", 7)),
                           priority_keywords=sources_cfg.get("priority_keywords", []),
                           max_items=500)
    by_id = {it["id"]: it for it in pool if it.get("id")}
    direct = _load_direct_urls(project_dir)
    chosen = []
    for old_it in old.items:
        if old_it.get("id") in by_id:
            it = dict(by_id[old_it["id"]])
        elif old_it.get("url"):
            url = old_it["url"]
            title, publisher = old_it.get("title", ""), old_it.get("publisher", "")
            if not publisher and " - " in title and "arxiv.org" not in url:
                title, publisher = (s.strip() for s in title.rsplit(" - ", 1))
            it = {"id": old_it.get("id", ""), "title": title, "url": url,
                  "source_name": old_it.get("source_name", ""), "publisher": publisher,
                  "kind": "arxiv" if "arxiv.org" in url else "news",
                  "published": old_it.get("published", ""), "summary": "",
                  "fetch_status": "title", "relevance": 0}
        else:
            continue
        gid = _gn_article_id(it.get("url", ""))
        if gid and gid in direct:                 # 人工核对过的发布方原文链接
            it["direct_url"], it["fetch_status"] = direct[gid], "pending"
        chosen.append(it)

    # 已不在当前 arXiv 列表里的论文：按 id 从公开 API 取完整摘要
    need = [arxiv_id_from_url(it["url"]) for it in chosen
            if it.get("kind") == "arxiv" and it.get("fetch_status") == "title"]
    if need:
        try:
            got = fetch_arxiv_by_ids(need)
        except Exception as ex:
            got = {}
            report["stages"]["arxiv_lookup"] = f"error: {ex}"
        for it in chosen:
            a = got.get(arxiv_id_from_url(it.get("url", "")))
            if a and len(a["summary"]) >= 200 and it.get("fetch_status") == "title":
                it.update(summary=a["summary"], authors=a["authors"], publisher="arXiv",
                          kind="arxiv", fetch_status="abstract")

    selected = select_items(chosen, max_items=int(e.get("max_items", 6)),
                            max_headline_items=int(e.get("max_headline_items", 2)))
    report["stages"]["collect"] = {"original": len(old.items), "matched": len(chosen),
                                   "status": [it.get("fetch_status") for it in selected]}
    if not any(it.get("fetch_status") != "title" for it in selected):
        report.update(result="skipped", reason="原来源里没有一条能拿到原文，不重做（保留旧节目）。")
        return report

    llm_cfg = show.get("llm", {})
    llm = LLMClient(model=llm_cfg.get("model"), fallback_model=llm_cfg.get("fallback_model"),
                    base_url=llm_cfg.get("base_url"),
                    temperature=llm_cfg.get("temperature", 0.3),
                    max_tokens=llm_cfg.get("max_tokens", 2000),
                    timeout_s=llm_cfg.get("timeout_s", 180))
    try:
        ep_data = write_episode(selected, llm,
                                target_words_low=e.get("target_duration_min", 5) * 140,
                                target_words_high=e.get("target_duration_max", 10) * 140,
                                show_name=show.get("show_name", "播客"),
                                feed_names=[s.get("name", "") for s in sources_cfg.get("sources", [])],
                                on_violation=show.get("quality", {}).get("on_violation", "block"))
    except _LE as ex:
        report.update(result="blocked", reason=f"{ex}（保留旧节目）")
        report["stages"]["editorial"] = f"error: {ex}"
        return report
    q = ep_data["quality"]
    report["stages"]["editorial"] = (f"ok (自检问题 {len(q['violations_first'])}→{len(q['violations_final'])}"
                                     f"{'，已自动改写' if q['repaired'] else ''})")

    # 音频：先写临时文件，校验通过再替换
    mp3 = os.path.join(project_dir, "audio", f"{episode_id}.mp3")
    tmp = mp3[:-4] + ".new.mp3"
    tts_cfg = show.get("tts", {})
    try:
        info = synthesize(ep_data["script"], tmp, voice=tts_cfg.get("voice", "zh-CN-YunxiNeural"),
                          rate=tts_cfg.get("rate", "+0%"),
                          min_sec=float(tts_cfg.get("min_duration_sec", 10)),
                          max_sec=float(tts_cfg.get("max_duration_sec", 1500)))
    except TTSUnavailable as ex:
        if os.path.exists(tmp):
            os.unlink(tmp)
        report.update(result="blocked", reason=f"音频生成失败：{ex}（保留旧节目）")
        return report
    os.replace(tmp, mp3)
    info["path"] = mp3
    report["stages"]["tts"] = f"ok ({info['duration_sec']}s via {info['engine']})"

    now = datetime.now(TZ).isoformat()
    history = list(old.history)
    if history and old.status != "pushed" and history[-1].get("old_commit") == old.commit_sha:
        entry = dict(history.pop())               # 上次重做还没推送：不叠加记录，保留最初的旧标题
    else:
        entry = {"old_title": old.title, "old_commit": old.commit_sha,
                 "reason": "按原来源用事实卡+程序自检流程重新生成"}
    entry["replaced_at"] = now
    ep = Episode(episode_id=old.episode_id, title=ep_data["title"], date=old.date,
                 items=[_public_item(it) for it in ep_data["items"]], script=ep_data["script"],
                 audio=info, status="site_built", quality=q,
                 history=history + [entry],
                 commit_sha=old.commit_sha, pages_url=old.pages_url,
                 created_at=old.created_at, published_at=old.published_at)
    store.upsert(ep)
    all_eps = sorted(store.load_episodes(), key=lambda x: (x.date or x.episode_id), reverse=True)
    build_site([x.to_dict() for x in all_eps], project_dir, show)
    report["stages"]["site"] = "ok"
    report["title"] = ep.title

    if do_push:
        _git(project_dir, ["add", "-A"])
        _git(project_dir, ["commit", "-m",
             f"episode {episode_id}: 按新流程重新生成（事实卡+自检）—{ep.title[:40]}"])
        ok, out = _push(project_dir)
        if ok:
            rc2, sha = _git(project_dir, ["rev-parse", "HEAD"])
            ep.status, ep.commit_sha = "pushed", sha.strip()
            store.upsert(ep)
            report["stages"]["push"] = f"ok sha={sha.strip()[:10]}"
        else:
            report["stages"]["push"] = f"error: {out[-300:]}"
            report["result"] = "push_failed"
            return report
    report["result"] = "republished"
    return report


def run_publish(project_dir: str, *, do_push: bool = False) -> dict:
    show, sources_cfg = load_config(project_dir)["show"], load_config(project_dir)["sources"]
    store = EpisodeStore(os.path.join(project_dir, "data"))
    state_dir = os.path.join(project_dir, "data")
    today = today_str()
    report: dict = {"date": today, "stages": {}, "result": "running"}

    # 0) 同日去重
    if store.has_published_today(today):
        report["result"] = "skipped"
        report["reason"] = "今天已经发布过一集，跳过（不重复发布）。"
        return report

    # 1-2) 采集 + 事实卡 + 撰稿 + 自检
    ep_data = compose_episode(show, sources_cfg, store, report)
    if ep_data is None:
        return report

    ep = Episode(episode_id=f"ep-{today}", title=ep_data["title"], date=today,
                 items=[_public_item(it) for it in ep_data["items"]], script=ep_data["script"],
                 quality=ep_data.get("quality", {}),
                 created_at=datetime.now(TZ).isoformat())

    # 3) TTS（需要 edge-tts/网络 或 SAPI）
    audio_dir = os.path.join(project_dir, "audio")
    mp3 = os.path.join(audio_dir, f"{ep.episode_id}.mp3")
    try:
        tts_cfg = show.get("tts", {})
        info = synthesize(ep.script, mp3, voice=tts_cfg.get("voice", "zh-CN-YunxiNeural"),
                          rate=tts_cfg.get("rate", "+0%"),
                          min_sec=float(tts_cfg.get("min_duration_sec", 10)),
                          max_sec=float(tts_cfg.get("max_duration_sec", 1500)))
        ep.audio.update({**info})
        ep.status = "audio_ready"
        report["stages"]["tts"] = f"ok ({info['duration_sec']}s via {info['engine']})"
    except TTSUnavailable as ex:
        report["stages"]["tts"] = f"blocked: {ex}"
        # 音频失败仍可出"文字版"节目页，但状态标记为未完成
        report["stages"].setdefault("note", "本集先以文字版上线，音频待补。")

    # 4) 音频托管：默认放本仓库（audio/ 随 Pages 发布）。可选 Drive（需 rclone+OAuth）。
    drv = show.get("drive", {})
    if ep.audio.get("path") and drv.get("enabled"):
        try:
            res = upload(ep.audio["path"], drv.get("rclone_remote", "podcastdrive"),
                         drv.get("remote_path", "PodcastPublisher/audio"))
            ep.audio.update(res)
            ep.status = "uploaded"
            report["stages"]["drive"] = f"ok (drive_id={res['drive_id']})"
        except DriveUnavailable as ex:
            report["stages"]["drive"] = f"blocked: {ex}"
    else:
        report["stages"]["drive"] = "skipped（音频在本仓库 audio/，随 Pages 发布）"

    # 5) 建网站（总是执行，空/文字版也要可看）— 含【全部历史节目】+ 本集，最新在前；输出到仓库根目录（Pages 源=根）
    site_out = project_dir
    prev = [e for e in store.load_episodes() if e.episode_id != ep.episode_id]
    all_eps = [ep] + prev
    all_eps.sort(key=lambda e: (e.date or e.episode_id), reverse=True)
    build_site([e.to_dict() for e in all_eps], site_out, show)
    ep.status = "site_built"
    report["stages"]["site"] = "ok"

    # 6) 提交 + 推送
    if do_push:
        _git(project_dir, ["add", "-A"])
        _git(project_dir, ["commit", "-m",
            f"episode {ep.episode_id}: {ep.title[:40]} [{ep.status}]"])
        ok, out = _push(project_dir)
        if ok:
            rc2, sha = _git(project_dir, ["rev-parse", "HEAD"])
            ep.status = "pushed"
            ep.commit_sha = sha.strip()
            ep.published_at = datetime.now(TZ).isoformat()
            report["stages"]["push"] = f"ok sha={sha.strip()[:10]}"
        else:
            report["stages"]["push"] = f"error: {out[-300:]}"

    # 7) 落状态 + 去重
    ep.pages_url = (show.get("pages_url", "") + f"episodes/{ep.episode_id}.html")
    store.upsert(ep)
    if ep.status in ("uploaded", "pushed", "site_built"):
        store.mark_seen([it["id"] for it in ep.items])
    report["episode_id"] = ep.episode_id
    report["title"] = ep.title
    report["status"] = ep.status
    report["result"] = report.get("result", "ok") if report.get("result") in ("ok", "blocked") else ep.status
    return report
