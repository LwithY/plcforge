param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) {
    $PythonPath = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    $PythonPath = (Get-Command python.exe -ErrorAction Stop).Source
}
& $PythonPath -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'; print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw 'Python check failed' }
@{python=$PythonPath} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot '.runtime.json') -Encoding UTF8
& (Join-Path $PSScriptRoot 'launch.ps1') init
