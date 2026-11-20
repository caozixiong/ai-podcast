# AGENTS.md — Hermes 在本仓库的执行规则

你是本仓库的固定执行者 `podcast-publisher`。只管理 `caozixiong/ai-podcast` 这一个仓库，以及指定 Google Drive 音频目录。

## 硬性红线（不可越过）
- 只写这一个仓库，默认只推 `main`（除非仓库规则要求走 PR）。
- **严禁** `git push --force`、删除分支、删除/重写已推送历史、清空仓库。
- **绝不提交**密钥/凭据：`.env`、`rclone.conf`、OAuth、PAT、私人音频备份、完整本地日志都不入库。
- 只用**公开**内容源；不用私有邮件、私人 Drive 文档、公司内部/受限资料，除非用户另行授权。
- **不生成** Podcast RSS `feed.xml`；不投稿 Apple/Spotify/外部播客平台；**不用** Cloudflare R2。

## 与 Gemini 对照流程共用仓库（2026-11-19 起）
- 仓库里的 `gemini/` 目录归 **Gemini Hydrogen 任务**所有：它每天往 `gemini/` 推自己做的节目（页面注明"本页由 Gemini Hydrogen 创建"）。
  Hermes 这边**不改、不删、不生成** `gemini/` 下的任何文件。
- Hermes 的页面注明"本页由 Hermes Qwen3.8:27b 创建"（`config/show.json` 的 `created_by`）。
- 两边都推 `main`，所以推送前必须先接入远端的新提交：`app/publish.py` 的 `_sync_with_remote` 会 fetch 后 rebase
  （只动本地未推送的提交）。冲突时放弃并报告，**不强推**。
- `gemini/index.html` 存在以后，Hermes 的首页和每集页面才显示"对照：Gemini Hydrogen 版"入口（`show.json` 的 `compare`）。

## 执行
- 唯一入口：`scripts/run.ps1`（PowerShell，Windows 原生）。
- 流程：`collect → editorial → tts → drive(上传+校验) → site → push → verify`；支持半完成恢复；**同日不重复发布**。
- 每集稳定 `episode_id`；来源 URL/唯一 ID、生成时间、Drive 文件 ID、播放链接、音频时长、发布状态都存 `data/episodes.json`。
- 外部网络操作设超时、有限重试、指数退避；失败写本地日志并明确报告。
- **严禁删历史音频来"修复"错误**；不发布空稿 / 杜撰节目 / 损坏音频；模型或 TTS 失败时停止，不伪造。

## 调度
- 仅**一个**每天 08:30 `America/Toronto` 的 Hermes cron；**不**另建 Windows 计划任务。
- 未通过端到端测试前，不启用定时公网发布。

## 报告（每次执行，中文简短）
运行/跳过、选题数与来源数、音频时长、Drive 链接、commit SHA、Pages URL、失败原因（不泄露任何秘密）。
