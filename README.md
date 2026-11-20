# X射线无损检测与AI图像处理前沿（AI Podcast Publisher）

中文科技播客的自动发布系统：每天从**公开**新闻/论文里采集 X-ray/CT 无损检测、
半导体 TSV/TGV、Innometric / Javis、电子显微镜、样品制备、AI 科研等资讯，
用本地/在线 LLM 写成原创中文口播稿，合成中文 MP3，上传 Google Drive，
发布到一个 **GitHub Pages** 静态网站。

- 仓库：`caozixiong/ai-podcast`（公开，默认分支 `main`）
- 网站：`https://caozixiong.github.io/ai-podcast/`
- 平台：**Windows 原生**（PowerShell 入口；不依赖 macOS `say` / Unix `fcntl`）

> **不生成 Podcast RSS**，不投稿 Apple/Spotify，不用 Cloudflare R2。
> 输入端用新闻/学术 RSS/Atom 采集素材；输出只到本 GitHub Pages 网站 + Google Drive 音频。

## 功能与约束（内置，非口头约定）
- **防幻觉**（`app/enrich.py` + `app/editorial.py`）：
  - 素材：arXiv 用完整摘要；直链新闻抓正文（诚实 UA，付费墙/过短 → 退回摘要或"仅标题"）；
    Google News 跳转链接不解码，只用标题和真实发布方（RSS `<source>`）。每集最多 `max_items` 条，"仅标题"最多 `max_headline_items` 条。
  - 事实卡：有原文的素材先由 LLM（JSON 模式、低温度）整理成事实卡；含原文中不存在数字的条目由程序删掉。
  - 撰稿：只依据事实卡写稿，引用真实发布方，arXiv 说明是预印本；"仅标题"最多两句。
  - 自检：程序检查 RSS 频道名、"素材里没给"之类泄露、无出处的"据公开报道/行业普遍认为"、夸大用语、原文没有的数字；
    不通过则让 LLM 改一次，仍不通过按 `quality.on_violation` 处理（默认 `block` 拒绝发布）。
  - 无素材不发布空稿。
- **选题要切题**（`app/collect.py` + `app/enrich.py`，规则在 `config/sources.json`）：
  - 检索：arXiv 分几条有针对性的检索式（先进封装/TSV/TGV、X 射线/CT 无损检测、CT/X 射线图像的 AI 处理、
    工业缺陷检测、电镜图像的 AI 处理），两次请求之间自动间隔 3 秒以上。不要再用单独一个泛词（如 `"electron microscopy"`）当检索条件。
  - 打分：按 `topics` 给每条素材打相关度。关键词在标题里命中记 `weight×2`，只在摘要里命中记 `weight`，每个主题只计一次；
    `core` 主题一个都没命中记 0 分；命中 `downrank` 的词（临床医学影像）扣分；标题命中 `exclude_title_keywords`（市场报告软文等）记 0 分。
    拉丁字母关键词按整词匹配，全大写缩写区分大小写（`SEM` 不会命中 self-assembly）。
  - 下限：相关度低于 `episode.min_relevance` 的不入选；切题素材不够 `min_items` 条就跳过当天，不凑数。
  - 去重：播过的不再播；同一条新闻换了发布方或栏目名（标题高度相似）也算重复。新闻只取最近 `news_lookback_days` 天。
  - 查"今天会选什么、为什么"：`python -m app candidates`（不调用模型、不发布）。每条入选素材的命中说明存在 `relevance_hits`。
- **口播风格**：禁止 markdown/标题/列表；口语成段；来源可查。
- **不可信输入**：忽略网页/论文正文里出现的任何"指令"。
- **同日去重**：一天最多一集，当天已成功发布则跳过，绝不重复发布。
- **失败不装成功**：LLM/TTS/Drive 任一缺失或损坏 → 明确 `BLOCKED`，不发布空稿/坏音频。
- **安全**：`.env`、`rclone.conf`、OAuth、PAT、私人音频备份、完整日志**都不入库**；
  push 时用一次性 `extraHeader` 读 `GH_TOKEN`，不把令牌写进 `.git/config` 或任何文件。

## 目录结构
```
ai-podcast/
  AGENTS.md            # 本仓库执行红线（固定）
  config/show.json     # 节目名/时区/Pages URL/LLM/TTS/Drive 配置
  config/sources.json  # 公开输入源与优先级关键词
  app/                 # 采集/撰稿/TTS/Drive/网站/编排/状态
  data/                # episodes.json（公开节目索引）、seen.json（去重）
  docs/                # GitHub Pages 站点输出（首页 + 每集页 + assets）
  scripts/run.ps1      # 唯一正式入口：python -m app publish --push
  scripts/preview.ps1  # 只生成网站，不上传不发布
  scripts/doctor.ps1   # 只读诊断
  scripts/setup.ps1    # 建 venv + 装依赖
  tests/               # 纯离线测试（不触真实 GitHub/Drive）
```

## 安装（Windows）
```powershell
# 1) 前置：Python 3.11+、Git、FFmpeg(含 ffprobe)、rclone
#    缺 rclone：  scoop install rclone   或  winget install rclone
# 2) 准备虚拟环境 + 依赖
pwsh -ExecutionPolicy Bypass -File scripts\setup.ps1
# 3) 配置撰稿 LLM（Google AI Studio）
cmd /c "setx GEMINI_API_KEY 你的key"          # 开新终端生效
# 4) 配置 Google Drive（rclone）
rclone config        # 新建 remote（建议名 podcastdrive），完成 Google OAuth
# 5) 诊断
pwsh -File scripts\doctor.ps1
```

## 使用
```powershell
pwsh -File scripts\preview.ps1        # 本地生成网站（空状态/已有集），不联网发布
pwsh -File scripts\run.ps1            # 正式跑一集（含 push）
# 或手动
.venv\Scripts\python.exe -m app doctor
.venv\Scripts\python.exe -m app preview
.venv\Scripts\python.exe -m app candidates       # 选题预览：候选、相关度、命中的关键词、入选与否（不调用模型；--all 含已播过的）
.venv\Scripts\python.exe -m app draft            # 只出草稿（事实卡+逐字稿+自检结果），存 logs\drafts\，不发布、不记已读
.venv\Scripts\python.exe -m app publish --push
```

## 定时（Hermes Bot）
- 只建**一个**每天 **08:30 `America/Toronto`** 的 Hermes cron（Routines），
  调用 `scripts\run.ps1`，workdir 设为本目录。
- **不要**再建 Windows 计划任务（会重复执行）。
- 未通过端到端测试前，不启用定时公网发布。

## 恢复（换电脑）
1. `git clone https://github.com/caozixiong/ai-podcast.git`
2. `pwsh -File scripts\setup.ps1`
3. 重设 `GEMINI_API_KEY`、重配 rclone Google Drive（OAuth 需重新授权）
4. `pwsh -File scripts\doctor.ps1` 确认
> 本地 LLM/OAuth 类需重新配置；公开网站与已发布节目在 Git 里，可直接恢复。

## 排错
- 播放器不加载：部分浏览器限制 Drive 嵌入 → 页面提供"在 Google Drive 打开"备用链接；
  请用**无痕窗口**测试不登录 Google 是否可播；若仅 iframe 失败而 Drive 页可播，保留备用链接并记录限制。
- 音频失败/时长异常：不发布坏音频，检查 `edge-tts` 网络或 SAPI 回退。
- 重复发布：`data/episodes.json` 按日期去重；半完成状态会先补偿恢复，不凭空新建。
