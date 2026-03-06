param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot ".venv\\Scripts\\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Error "Python from venv not found: $pythonExe"
    exit 2
}

# Isolate pytest temp/cache under project dir.
$workRoot = Join-Path $repoRoot "test_runtime"
$tempRoot = Join-Path $workRoot "tmp"
$baseTemp = Join-Path $workRoot "basetemp"

New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
New-Item -ItemType Directory -Path $baseTemp -Force | Out-Null

$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:PYTEST_DEBUG_TEMPROOT = $tempRoot

# Some environments lock pytest-of-<real_user>; use isolated username for pytest process.
$env:USERNAME = "runner"
$env:USER = "runner"

$argsList = @(
    "-m", "pytest",
    "--basetemp", $baseTemp,
    "-p", "no:cacheprovider"
)
if ($PytestArgs -and $PytestArgs.Count -gt 0) {
    $argsList += $PytestArgs
}

Write-Host "Running: $pythonExe $($argsList -join ' ')"
& $pythonExe @argsList
exit $LASTEXITCODE
