# 只读诊断：工具/依赖/key/配置/状态。python -m app doctor
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (Test-Path (Join-Path $root ".venv\Scripts\python.exe")) { $py = (Join-Path $root ".venv\Scripts\python.exe") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
else { throw "找不到 python" }
& $py -m app doctor
exit $LASTEXITCODE
