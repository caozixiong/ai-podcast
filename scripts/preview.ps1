# 本地预览：只生成网站，不上传、不发布。python -m app preview
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (Test-Path (Join-Path $root ".venv\Scripts\python.exe")) { $py = (Join-Path $root ".venv\Scripts\python.exe") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
else { throw "找不到 python，请先运行 scripts\setup.ps1" }
& $py -m app preview
exit $LASTEXITCODE
