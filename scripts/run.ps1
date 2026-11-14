# 唯一正式执行入口（Windows 原生 PowerShell）：python -m app publish --push
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Resolve-Python {
  if (Test-Path (Join-Path $root ".venv\Scripts\python.exe")) { return (Join-Path $root ".venv\Scripts\python.exe") }
  if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
  throw "找不到 python，请先运行 scripts\setup.ps1"
}

$py = Resolve-Python
& $py -m app publish --push
exit $LASTEXITCODE
