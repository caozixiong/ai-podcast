"""静态网站生成器（响应式 · 空状态友好 · 兼容 /ai-podcast/ 子路径）。

- 首页：节目名、日期、摘要、播放入口、搜索过滤、全部历史节目
- 每集页：iframe 播放器 + Drive 备用链接 + 原文来源 + 逐字稿 + AI 内容说明
- 无节目时显示友好空状态，绝不伪造样例新闻或假 MP3
- 资源用【相对】路径，避免 /assets/ 绝对路径在子目录下 404
"""
from __future__ import annotations

import html
import os


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _ep_summary(ep: dict, limit: int = 160) -> str:
    parts = [it.get("title", "") for it in ep.get("items", []) if it.get("title")]
    text = " · ".join(parts)
    return (text[:limit] + "…") if len(text) > limit else text


def _creator(ep: dict, cfg: dict) -> str:
    """这一集是谁做的（哪个 bot、哪个模型）。发布时记在节目里；老节目没记，用配置里的当前值。"""
    return (ep.get("quality") or {}).get("created_by") or cfg.get("created_by", "")


def _compare_link(cfg: dict, prefix: str = "") -> str:
    """可选：指向另一套流程做的对照版本（如 Gemini 版）。show.json 里没配 compare 就不显示。"""
    c = cfg.get("compare") or {}
    url = c.get("url", "")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = prefix + url
    return f'<a class="tag" href="{_esc(url)}">{_esc(c.get("label") or "对照版本")}</a>'


def _dur(ep: dict) -> str:
    d = ep.get("audio", {}).get("duration_sec")
    return f"{int(d // 60)}:{int(d % 60):02d}" if d else "音频待上传"


def _episode_card(ep: dict, cfg: dict | None = None) -> str:
    eid = _esc(ep.get("episode_id"))
    title = _esc(ep.get("title"))
    by = _creator(ep, cfg or {})
    by_html = f" · {_esc(by)} 创建" if by else ""
    return f"""
    <article class="ep" data-search="{_esc(title)} {_esc(_ep_summary(ep, 80))}">
      <div class="ep-head">
        <h3><a href="episodes/{eid}.html">{title}</a></h3>
        <span class="meta">{_esc(ep.get('date'))} · {_dur(ep)}{by_html}</span>
      </div>
      <p class="ep-sum">{_esc(_ep_summary(ep))}</p>
      <div class="ep-actions">
        <a class="btn play" href="episodes/{eid}.html">▶ 收听</a>
        <a class="link" href="episodes/{eid}.html">逐字稿与来源</a>
      </div>
    </article>"""


CSS = """
:root{--bg:#0e1116;--card:#161b23;--fg:#e6ebf2;--muted:#93a0b4;--accent:#5aa9ff;--border:#232c3a;--pill:#1d2635}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:820px;margin:0 auto;padding:24px 20px 60px}
header.hero{padding:20px 0 8px}
h1{font-size:2rem;margin:.1em 0}
.sub{color:var(--muted);margin:0 0 8px}
.tag{display:inline-block;background:var(--pill);color:var(--muted);border:1px solid var(--border);
  border-radius:999px;padding:2px 10px;font-size:.8rem;margin:2px 4px 2px 0}
.by{margin:6px 0 0}
.tag.by-tag{color:var(--fg);border-color:var(--accent)}
a.tag:hover{text-decoration:none;border-color:var(--accent);color:var(--accent)}
.search{margin:22px 0;position:relative}
.search input{width:100%;padding:12px 14px;border-radius:12px;border:1px solid var(--border);
  background:var(--card);color:var(--fg);font-size:1rem;outline:none}
.search input:focus{border-color:var(--accent)}
.ep{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:18px 18px;margin:14px 0}
.ep-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.ep h3{margin:0;font-size:1.15rem}
.meta{color:var(--muted);font-size:.85rem;white-space:nowrap}
.ep-sum{color:var(--muted);margin:10px 0}
.ep-actions{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.btn{display:inline-block;background:var(--accent);color:#04101f;font-weight:600;
  padding:8px 16px;border-radius:10px}
.btn:hover{text-decoration:none;filter:brightness(1.08)}
.link{color:var(--accent);font-size:.92rem}
.empty{background:var(--card);border:1px solid var(--border);border-radius:16px;
  padding:40px 24px;text-align:center;margin-top:12px}
.empty-ico{font-size:2.6rem}
.empty h2{margin:.2em 0}
.hint{color:var(--muted);font-size:.85rem}
.player iframe{width:100%;height:140px;border:0;border-radius:12px;background:#000}
.player .note{color:var(--muted);font-size:.82rem;margin:8px 0 0}
.actions{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}
h2.sec{border-bottom:1px solid var(--border);padding-bottom:6px;margin:26px 0 12px;font-size:1.2rem}
ol.src{padding-left:20px}ol.src li{margin:8px 0;word-break:break-word}
.script p{margin:12px 0}
.ai-note{background:var(--pill);border:1px solid var(--border);border-radius:12px;
  padding:12px 14px;color:var(--muted);font-size:.85rem;margin-top:20px}
footer{color:var(--muted);font-size:.8rem;margin-top:40px;text-align:center}
#pager{display:flex;align-items:center;justify-content:center;gap:14px;margin:22px 0 6px;flex-wrap:wrap}
.pgbtn{background:var(--card);color:var(--fg);border:1px solid var(--border);border-radius:10px;padding:8px 16px;font-size:.95rem;cursor:pointer;font-family:inherit}
.pgbtn:hover{border-color:var(--accent);color:var(--accent)}
#pageinfo{color:var(--muted);font-size:.85rem}
.noresult{color:var(--muted);text-align:center;padding:24px}
@media(max-width:520px){h1{font-size:1.6rem}.wrap{padding:18px 14px 48px}}
"""

JS = """
(function(){
  var list=document.getElementById('list');
  if(!list) return;
  var items=Array.prototype.slice.call(list.querySelectorAll('.ep'));
  var PER=5, page=0;
  var prev=document.getElementById('prev'), next=document.getElementById('next');
  var info=document.getElementById('pageinfo'), pager=document.getElementById('pager');
  var nores=document.getElementById('noresult');
  var pages=Math.max(1, Math.ceil(items.length/PER));
  function applyPage(){
    items.forEach(function(el,i){ el.style.display=(i>=page*PER && i<page*PER+PER)?'':'none'; });
    info.textContent='第 '+(page+1)+' / '+pages+' 页 · 每页 '+PER+' 篇';
    prev.style.display=(page>0)?'':'none';
    next.style.display=(page<pages-1)?'':'none';
    if(pager) pager.style.display='';
  }
  if(prev) prev.onclick=function(){ if(page>0){page--;applyPage();} };
  if(next) next.onclick=function(){ if(page<pages-1){page++;applyPage();} };
  var sbox=document.getElementById('search');
  var inp=(sbox&&sbox.tagName==='INPUT')?sbox:(sbox?sbox.querySelector('input'):null);
  if(inp){
    inp.addEventListener('input',function(){
      var q=inp.value.trim().toLowerCase();
      if(q){
        var hit=0;
        items.forEach(function(el){
          var ok=(el.getAttribute('data-search')||'').toLowerCase().indexOf(q)>=0;
          el.style.display=ok?'':'none'; if(ok)hit++;
        });
        if(pager) pager.style.display='none';
        if(nores) nores.style.display=hit?'none':'';
      } else {
        page=0; if(nores) nores.style.display='none'; applyPage();
      }
    });
  }
  applyPage();
})();
"""


def build_index(episodes: list[dict], cfg: dict) -> str:
    show = _esc(cfg.get("show_name", "播客"))
    sub = _esc(cfg.get("subtitle", ""))
    by = cfg.get("created_by", "")
    by_tag = f'<span class="tag by-tag">本站节目由 {_esc(by)} 创建</span>' if by else ""
    by_foot = f" · 由 {_esc(by)} 创建" if by else ""
    if episodes:
        cards = "\n".join(_episode_card(ep, cfg) for ep in episodes)
        list_html = (
            f'<div id="list">{cards}</div>'
            '<div id="pager"><button id="prev" class="pgbtn">← 上一页</button>'
            '<span id="pageinfo"></span>'
            '<button id="next" class="pgbtn">下一页 →</button></div>'
            '<p id="noresult" class="noresult" style="display:none">没有找到匹配的节目。</p>'
        )
    else:
        list_html = (
            '<div class="empty"><div class="empty-ico">📡</div>'
            '<h2>节目正在筹备中</h2>'
            '<p>我们每天从公开新闻与论文里挑选 X-ray/CT 无损检测、TSV/TGV、'
            '半导体检测与 AI 图像处理的好内容。第一集上线后会出现在这里。</p>'
            '<p class="hint">（当前还没有已发布的节目 —— 这是真实空状态，非占位内容）</p></div>'
        )
    return f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{show} · {_esc(cfg.get('subtitle',''))}</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<div class="wrap">
  <header class="hero">
    <h1>{show}</h1>
    <p class="sub">{sub}</p>
    <div><span class="tag">X-ray / CT 无损检测</span><span class="tag">TSV / TGV</span><span class="tag">半导体检测</span><span class="tag">AI 图像处理</span></div>
    <p class="by">{by_tag}{_compare_link(cfg)}</p>
  </header>
  <div class="search"><input id="search" type="search" placeholder="搜索节目（标题 / 关键词，如 TSV、Innometric）" aria-label="搜索节目"></div>
  {list_html}
  <footer>《{show}》 · 公开资讯的中文口播解读 · 由 AI 辅助生成并人工约束来源{by_foot}</footer>
</div>
<script>{JS}</script>
</body></html>
"""


AI_NOTE_LEGACY = ('本节目由 AI 基于上述公开来源辅助生成，并受"只用给定素材、不编造数值、来源可查"的约束；'
                  '音频若无法嵌入播放，请通过 Google Drive 备用链接收听。节目内容仅供学习交流，不构成任何技术/投资建议。')
AI_NOTE_CHECKED = ('本节目由 AI 基于上述公开来源辅助生成：论文依据 arXiv 摘要（预印本，未经同行评审），'
                   '新闻依据可获取的正文；标注"仅标题"的条目只读取了标题。逐字稿发布前已由程序核对数字、出处和禁用说法，'
                   '但仍可能有理解偏差，请以原文为准。节目内容仅供学习交流，不构成任何技术/投资建议。')
_STATUS_NOTE = {"title": "（仅标题）"}


def _source_label(it: dict) -> str:
    if it.get("kind") == "arxiv":
        return "arXiv 预印本"
    return it.get("publisher") or it.get("source_name") or ""


def build_episode(ep: dict, cfg: dict) -> str:
    eid = str(ep.get("episode_id", "")).replace("/", "_")
    title = _esc(ep.get("title"))
    audio = ep.get("audio", {})
    preview = audio.get("preview_url", "")
    share = audio.get("share_url", preview)

    if preview and share:
        # Drive 模式（可选升级）
        player = (
            f'<div class="player"><iframe src="{_esc(preview)}" allow="autoplay" '
            f'title="{title} 播放器" style="width:100%;height:140px;border:0;border-radius:12px;background:#000"></iframe>'
            f'<p class="note">播放器未加载？用下面的备用链接；在无痕窗口测试不登录 Google 是否可播。</p></div>'
        )
        actions = f'<a class="btn play" href="{_esc(share)}" target="_blank" rel="noopener">▶ 在 Google Drive 打开</a>'
    elif audio.get("path") or audio.get("duration_sec"):
        # 本仓库托管：audio/{eid}.mp3（相对 episodes/ 页 = ../audio/）
        player = (
            f'<div class="player"><audio controls preload="metadata" '
            f'src="../audio/{eid}.mp3" style="width:100%"></audio>'
            f'<p class="note">音频随本仓库发布；若无法播放，请刷新或稍后重试。</p></div>'
        )
        actions = f'<a class="btn play" href="../audio/{eid}.mp3" download="{eid}.mp3">⬇ 下载 MP3</a>'
    else:
        player = ('<div class="empty"><h2>音频生成中</h2>'
                  '<p class="hint">本集音频尚在生成，暂不可播放。逐字稿与来源见下。</p></div>')
        actions = ''

    srcs = "".join(
        f'<li><a href="{_esc(it.get("url"))}" target="_blank" rel="noopener">{_esc(it.get("title"))}</a>'
        f' <span class="hint">— {_esc(_source_label(it))} · {_esc(it.get("published") or "日期未知")}'
        f'{_esc(_STATUS_NOTE.get(it.get("fetch_status"), ""))}</span></li>'
        for it in ep.get("items", []) if it.get("url")) or '<li class="hint">（本集无外链来源）</li>'
    ai_note = AI_NOTE_CHECKED if ep.get("quality") else AI_NOTE_LEGACY

    by = _creator(ep, cfg)
    by_tag = f'<span class="tag by-tag">本页由 {_esc(by)} 创建</span>' if by else ""
    by_note = f"本页由 {_esc(by)} 创建。" if by else ""

    script = ep.get("script", "")
    # 逐字稿按空行/句号粗略分段
    paras = [p.strip() for p in script.replace("\r\n", "\n").split("\n\n") if p.strip()]
    if not paras:
        paras = [script] if script else []
    script_html = "".join(f"<p>{_esc(p)}</p>" for p in paras) or '<p class="hint">（暂无逐字稿）</p>'

    return f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · {_esc(cfg.get('show_name',''))}</title>
<link rel="stylesheet" href="../assets/style.css">
</head>
<body>
<div class="wrap">
  <p><a href="../">← 返回首页</a></p>
  <header class="hero"><h1>{title}</h1><p class="sub">{_esc(ep.get('date'))} · {_dur(ep)}</p>
    <p class="by">{by_tag}{_compare_link(cfg, "../")}</p></header>
  {player}
  <div class="actions">{actions}<a class="link" href="#" onclick="window.print();return false;">打印/复制逐字稿</a></div>
  <h2 class="sec">本期来源（公开信息）</h2>
  <ol class="src">{srcs}</ol>
  <h2 class="sec">逐字稿</h2>
  <div class="script">{script_html}</div>
  <div class="ai-note">{by_note}{ai_note}</div>
</div>
</body></html>
"""


def build_site(episodes: list[dict], out_dir: str, cfg: dict) -> dict:
    """生成 index.html + assets + 每集页。返回写出的文件列表。

    只写 index.html、assets/、episodes/、audio/、.nojekyll；不碰 gemini/（那是 Gemini 对照流程的目录）。
    """
    need = (cfg.get("compare") or {}).get("only_if_exists")
    if need and not os.path.exists(os.path.join(out_dir, need)):
        cfg = {k: v for k, v in cfg.items() if k != "compare"}      # 对照版本还没上线：先不放入口，免得死链
    os.makedirs(os.path.join(out_dir, "assets"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "episodes"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "audio"), exist_ok=True)
    with open(os.path.join(out_dir, "assets", "style.css"), "w", encoding="utf-8") as f:
        f.write(CSS)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_index(episodes, cfg))
    for ep in episodes:
        eid = ep.get("episode_id")
        if not eid:
            continue
        safe = str(eid).replace("/", "_")
        with open(os.path.join(out_dir, "episodes", f"{safe}.html"), "w", encoding="utf-8") as f:
            f.write(build_episode(ep, cfg))
    with open(os.path.join(out_dir, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")
    return {"out_dir": out_dir, "episodes": len(episodes)}
