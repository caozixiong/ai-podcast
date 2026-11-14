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
- **防幻觉**：LLM 只用给定素材，禁止编造数字/指标/公司名/工具名/实验结果；无素材不发布空稿。
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
