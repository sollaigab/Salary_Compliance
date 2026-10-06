# Registers (or updates) the scheduled task for the weekly crawl: every Monday at 7:00.
# If the PC is off at that time, the task starts as soon as possible (StartWhenAvailable).
#
# Usage:   powershell -ExecutionPolicy Bypass -File scripts\register_weekly_crawl.ps1
# Remove:  Unregister-ScheduledTask -TaskName "Salary Observatory - weekly crawl" -Confirm:$false

$Name = "Salary Observatory - weekly crawl"
$Script = Join-Path $PSScriptRoot "weekly_crawl.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 7:00
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Downloads the job ads and updates the exports of the salary transparency observatory" -Force |
    Out-Null

Get-ScheduledTask -TaskName $Name | Get-ScheduledTaskInfo |
    Select-Object TaskName, NextRunTime | Format-List
