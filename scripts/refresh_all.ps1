param(
    [switch]$SkipCollector
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PyExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PyExe)) {
    $PyExe = "python"
}

$CmdArgs = @("-m", "autotrade_v3.reconcile_v3", "refresh", "--repo-root", $RepoRoot)
if ($SkipCollector) {
    $CmdArgs += "--skip-collector"
}

& $PyExe @CmdArgs
exit $LASTEXITCODE
