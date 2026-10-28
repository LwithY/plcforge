$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeFile = Join-Path $projectRoot '.runtime.json'
$pythonCandidates = @()
if (Test-Path -LiteralPath $runtimeFile) {
    $pythonCandidates += (Get-Content -LiteralPath $runtimeFile -Raw | ConvertFrom-Json).python
}
$pythonCandidates += (Join-Path $projectRoot '.venv\Scripts\python.exe')
$pythonCandidates += (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pythonCommand) { $pythonCandidates += $pythonCommand.Source }
$selectedPython = $null
foreach ($candidate in $pythonCandidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
        & $candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $selectedPython = $candidate; break }
    }
}
if (-not $selectedPython) { throw 'Python 3.11+ is required. Install Python and run scripts\setup.ps1.' }
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $projectRoot + [IO.Path]::PathSeparator + $previousPythonPath
    & $selectedPython -m plcforge @args
    $commandExitCode = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
exit $commandExitCode
