# Weekly crawl of the observatory: updates the job ads and rebuilds the exports.
# Run by Windows Task Scheduler (see scripts\register_weekly_crawl.ps1).
#
# Manual use:
#   powershell -ExecutionPolicy Bypass -File scripts\weekly_crawl.ps1           # real crawl
#   powershell -ExecutionPolicy Bypass -File scripts\weekly_crawl.ps1 -DryRun   # check only, no requests

param([switch]$DryRun)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "$env:LOCALAPPDATA\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Log = Join-Path $LogDir ("crawl_" + (Get-Date -Format "yyyy-MM-dd_HHmm") + ".log")

function Write-Log($text) {
    $line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "  " + $text
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

$env:PYTHONIOENCODING = "utf-8"
Write-Log "Weekly crawl started (folder: $Root)"

if ($DryRun) {
    Write-Log ("Dry run: Python found -> " + (& $Python --version 2>&1))
    & $Python -c "from lib import db; c = db.connect(); print('job ads in the DB:', c.execute('select count(*) from jobs').fetchone()[0])" 2>&1 |
        ForEach-Object { Write-Log $_ }
    Write-Log "Dry run finished: no requests sent"
    Write-Output "Log: $Log"
    exit 0
}

# 1) job ads (no --cache: always fresh data). robots.txt, TDM reservation and pauses live in lib/http.py
& $Python -u crawl_jobs.py 2>&1 | ForEach-Object { Write-Log $_ }
$crawlExit = $LASTEXITCODE

# 2) exports for analysis and the web app
& $Python export.py duckdb 2>&1 | ForEach-Object { Write-Log $_ }
& $Python export.py public 2>&1 | ForEach-Object { Write-Log $_ }

Write-Log "Done (crawl exit code: $crawlExit)"
exit $crawlExit
