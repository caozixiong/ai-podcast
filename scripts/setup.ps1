# 一次性本地环境准备：建虚拟环境 + 安装依赖。
# 用法：pwsh -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# 找 python
$py = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
else { throw "找不到 python（建议安装 Python 3.11+）"; }

# 建 venv
$venv = Join-Path $root ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
  & $py -m venv $venv
}
$pyexe = Join-Path $venv "Scripts\python.exe"

& $pyexe -m pip install --upgrade pip
& $pyexe -m pip install -r (Join-Path $root "requirements.txt")

# 检查关键二进制
foreach ($b in @("ffmpeg","ffprobe","rclone","git")) {
  if (Get-Command $b -ErrorAction SilentlyContinue) { Write-Host "  $b : OK" }
  else { Write-Warning "  $b : 缺失（rclone 需配合 Google Drive；ffmpeg 需 TTS 转码）" }
}
Write-Host "setup 完成。下一步：设 GEMINI_API_KEY，运行 scripts\doctor.ps1"
