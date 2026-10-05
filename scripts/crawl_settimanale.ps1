# Crawl settimanale dell'osservatorio: aggiorna gli annunci e rigenera gli export.
# Lanciato dall'Utilità di pianificazione di Windows (vedi scripts/registra_crawl_settimanale.ps1).
#
# Uso manuale:
#   powershell -ExecutionPolicy Bypass -File scripts\crawl_settimanale.ps1           # crawl vero
#   powershell -ExecutionPolicy Bypass -File scripts\crawl_settimanale.ps1 -Prova    # solo verifica, niente richieste

param([switch]$Prova)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "$env:LOCALAPPDATA\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Log = Join-Path $LogDir ("crawl_" + (Get-Date -Format "yyyy-MM-dd_HHmm") + ".log")

function Scrivi($testo) {
    $riga = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "  " + $testo
    Add-Content -Path $Log -Value $riga -Encoding UTF8
}

$env:PYTHONIOENCODING = "utf-8"
Scrivi "Inizio crawl settimanale (cartella: $Root)"

if ($Prova) {
    Scrivi ("Prova: Python trovato -> " + (& $Python --version 2>&1))
    & $Python -c "from lib import db; c = db.connect(); print('annunci nel DB:', c.execute('select count(*) from jobs').fetchone()[0])" 2>&1 |
        ForEach-Object { Scrivi $_ }
    Scrivi "Prova finita: nessuna richiesta inviata"
    Write-Output "Log: $Log"
    exit 0
}

# 1) annunci (senza --cache: dati sempre freschi). Robots.txt, riserva TDM e pause sono in lib/http.py
& $Python -u crawl_jobs.py 2>&1 | ForEach-Object { Scrivi $_ }
$crawlExit = $LASTEXITCODE

# 2) export per analisi e web app
& $Python export.py duckdb 2>&1 | ForEach-Object { Scrivi $_ }
& $Python export.py public 2>&1 | ForEach-Object { Scrivi $_ }

Scrivi "Fine (codice uscita crawl: $crawlExit)"
exit $crawlExit
