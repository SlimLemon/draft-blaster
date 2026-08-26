<#
.SYNOPSIS
  Launch Draft Copilot with optional selftest.

.DESCRIPTION
  Runs the automated dry-run on a COPY of the workbook (isolated profile),
  then launches the live co-pilot if the test passes.

.PARAMETER SkipTest
  Skip the dry-run and go straight to live mode.

.EXAMPLE
  .\launch_draft.ps1
  .\launch_draft.ps1 -SkipTest
#>
param(
    [switch]$SkipTest
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$LoPython = "C:\Program Files\LibreOffice\program\python.exe"
$LiveBook = Join-Path $ScriptDir "Draft_Command_Center_DRAFT_DAY.xlsx"
$TestCopy = Join-Path $ScriptDir "draft_test_copy.xlsx"

# ── Preflight ──────────────────────────────────────────────────────
if (!(Test-Path $LiveBook)) {
    Write-Host "ERROR: Workbook not found at $LiveBook" -ForegroundColor Red
    exit 1
}
if (!(Test-Path $LoPython)) {
    Write-Host "ERROR: LibreOffice python not found at $LoPython" -ForegroundColor Red
    exit 1
}

$ConfigPath = Join-Path $ScriptDir "espn_config.json"
$HasEnvCookies = $env:DRAFT_COPILOT_ESPN_S2 -and $env:DRAFT_COPILOT_SWID
if (Test-Path $ConfigPath) {
    Write-Host "espn_config.json: present (values not printed)"
} elseif ($HasEnvCookies) {
    Write-Host "espn_config.json: missing, but DRAFT_COPILOT_ESPN_S2 / DRAFT_COPILOT_SWID are set"
} else {
    Write-Host "WARNING: no espn_config.json and no cookie env vars — watch will fail; manual who/sales still work" -ForegroundColor Yellow
}

# Refuse to launch if something else already owns the default UNO port.
$portBusy = $false
try {
    $listeners = Get-NetTCPConnection -LocalPort 2002 -State Listen -ErrorAction SilentlyContinue
    if ($listeners) { $portBusy = $true }
} catch {
    $net = netstat -ano | Select-String ':2002\s+.*LISTENING'
    if ($net) { $portBusy = $true }
}
if ($portBusy) {
    Write-Host "ERROR: port 2002 already in use. Close LibreOffice / other co-pilot first." -ForegroundColor Red
    exit 1
}

# ── Selftest (unless skipped) ──────────────────────────────────────
if (!$SkipTest) {
    Write-Host "=== DRAFT CO-PILOT DRY RUN ===" -ForegroundColor Cyan
    Write-Host "Copying workbook to test copy..."
    Copy-Item -LiteralPath $LiveBook -Destination $TestCopy -Force

    Write-Host "Running selftest in isolated profile..."
    & $LoPython draft_copilot.py --test $TestCopy
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        Write-Host ""
        Write-Host "SELFTEST FAILED (exit code $exitCode). Fix issues before going live." -ForegroundColor Red
        Write-Host "Journal: journal.txt" -ForegroundColor Yellow
        exit $exitCode
    }
    Write-Host ""
    Write-Host "SELFTEST PASSED" -ForegroundColor Green
}

# ── Live launch ────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== LAUNCHING LIVE CO-PILOT ===" -ForegroundColor Cyan
Write-Host "Workbook: $LiveBook"
Write-Host "Type 'watch' after READY to start ESPN sync."
Write-Host ""
& $LoPython draft_copilot.py
