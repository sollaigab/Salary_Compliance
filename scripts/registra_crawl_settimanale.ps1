# Registra (o aggiorna) l'attività pianificata del crawl settimanale: ogni lunedì alle 7:00.
# Se il PC è spento a quell'ora, l'attività parte appena possibile (StartWhenAvailable).
#
# Uso:      powershell -ExecutionPolicy Bypass -File scripts\registra_crawl_settimanale.ps1
# Rimuovi:  Unregister-ScheduledTask -TaskName "Osservatorio RAL - crawl settimanale" -Confirm:$false

$Nome = "Osservatorio RAL - crawl settimanale"
$Script = Join-Path $PSScriptRoot "crawl_settimanale.ps1"

$Azione = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$Quando = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 7:00
$Impostazioni = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $Nome -Action $Azione -Trigger $Quando -Settings $Impostazioni `
    -Description "Scarica gli annunci di lavoro e aggiorna gli export dell'osservatorio retribuzioni" -Force |
    Out-Null

Get-ScheduledTask -TaskName $Nome | Get-ScheduledTaskInfo |
    Select-Object TaskName, NextRunTime | Format-List
