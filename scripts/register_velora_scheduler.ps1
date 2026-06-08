# Enregistre une tâche planifiée Windows : run_all.py toutes les 30 minutes.
# Exécuter une fois en PowerShell (idéalement « Exécuter en tant qu'administrateur ») :
#   Set-ExecutionPolicy -Scope Process Bypass -Force
#   .\scripts\register_velora_scheduler.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
if (-not $Python) {
    $Python = (Get-Command py -ErrorAction SilentlyContinue)?.Source
    if ($Python) { $Python = "$Python -3" }
}
if (-not $Python) {
    Write-Error "Python introuvable dans le PATH."
}

$RunAll = Join-Path $RepoRoot "run_all.py"
$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "velora_pipeline.log"

$TaskName = "VeloraAutoPipeline"
$Action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$Python`" -u `"$RunAll`" >> `"$LogFile`" 2>&1" `
    -WorkingDirectory $RepoRoot

$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration ([TimeSpan]::MaxValue)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null

Write-Host "Tâche '$TaskName' enregistrée (toutes les 30 min)."
Write-Host "  Script : $RunAll"
Write-Host "  Logs   : $LogFile"
Write-Host "Vérifier : Get-ScheduledTask -TaskName $TaskName"
Write-Host "Lancer   : Start-ScheduledTask -TaskName $TaskName"
