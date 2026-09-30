#Requires -Version 5.1
<#
.SYNOPSIS
    Register the Jarvis autostart task in Windows Task Scheduler (roadmap T0.6, step 2).

.DESCRIPTION
    Creates a scheduled task that runs start-jarvis.ps1 at user logon:
      * Trigger  : AtLogOn (current user only)
      * RunLevel : Limited  (no UAC elevation prompt)
      * Restart  : up to 2 times, 1 minute apart, if the launcher fails
      * The task runs powershell.exe with -ExecutionPolicy Bypass so the
        launcher is not blocked by the machine execution policy.

    Registering a task under Task Scheduler requires administrative rights
    ONCE (creation only). The task itself runs Limited / non-elevated, so
    Jarvis starts without a UAC prompt every logon.

    Re-running this script re-registers (overwrites) the task cleanly.

.NOTES
    Run this from an ELEVATED PowerShell:
        powershell -ExecutionPolicy Bypass -File .\register-task.ps1

    To remove the task later:
        Unregister-ScheduledTask -TaskName 'JarvisAutostart' -Confirm:$false
#>

[CmdletBinding()]
param(
    [string]$TaskName   = 'JarvisAutostart',
    [string]$ProjectDir = 'C:\Users\serj\Jarvis',
    [string]$ScriptName = 'start-jarvis.ps1'
)

$ErrorActionPreference = 'Stop'

$ScriptPath = Join-Path $ProjectDir $ScriptName

# --- Sanity checks ---------------------------------------------------------
if (-not (Test-Path $ScriptPath)) {
    Write-Error ("Autostart script not found: {0}" -f $ScriptPath)
    exit 1
}

# Verify we are elevated (task creation needs admin).
$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error 'This script must be run from an ELEVATED (Run as administrator) PowerShell to register the task.'
    exit 1
}

Write-Host ("Registering task '{0}' -> {1}" -f $TaskName, $ScriptPath)

# --- Action: run the launcher via powershell.exe, bypassing exec policy ----
$psExe  = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $psExe `
            -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $ScriptPath) `
            -WorkingDirectory $ProjectDir

# --- Trigger: at logon of the CURRENT user ---------------------------------
$currentUser = "$env:USERDOMAIN\$env:USERNAME"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser

# --- Principal: run as current user, Limited (no elevation) ----------------
$principal = New-ScheduledTaskPrincipal -UserId $currentUser `
                -LogonType Interactive -RunLevel Limited

# --- Settings: auto-restart 2x, don't stop on idle/battery -----------------
$settings = New-ScheduledTaskSettingsSet `
                -RestartCount 2 `
                -RestartInterval (New-TimeSpan -Minutes 1) `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -StartWhenAvailable `
                -ExecutionTimeLimit (New-TimeSpan -Hours 0)   # 0 = no time limit

# --- Register (overwrite if it already exists) -----------------------------
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host 'Existing task found; re-registering (overwrite).'
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Autostart local J.A.R.V.I.S. assistant at user logon (roadmap T0.6).' | Out-Null

Write-Host ''
Write-Host ("OK: task '{0}' registered." -f $TaskName)
Write-Host 'Trigger : AtLogOn (this user)'
Write-Host 'RunLevel: Limited (no UAC prompt)'
Write-Host 'Restart : up to 2x, 1 min apart'
Write-Host ''
Write-Host 'Verify with:'
Write-Host ("  Get-ScheduledTask -TaskName '{0}' | Format-List TaskName,State" -f $TaskName)
Write-Host 'Test now (runs the launcher immediately) with:'
Write-Host ("  Start-ScheduledTask -TaskName '{0}'" -f $TaskName)
