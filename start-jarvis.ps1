#Requires -Version 5.1
<#
.SYNOPSIS
    Autostart script for the local J.A.R.V.I.S. assistant (roadmap task T0.6).

.DESCRIPTION
    Boot sequence:
      1. Ensure the Ollama server is up (start it if the API is not answering).
      2. Wait for the Ollama API to become ready (up to 30 s).
      3. Warm up the model from Jarvis config.json (llm.model) so the first real request is fast.
      4. Activate the project virtualenv.
      5. Launch Jarvis (python -m jarvis) in wake-word standby.

    All output is teed to logs\autostart_<timestamp>.log.
    The script fails honestly: if Ollama is not ready within the timeout it
    exits with code 1 and a clear log line, instead of hanging.

    NOTE: Jarvis is expected to start in wake-word STANDBY (not active
    listening). That behavior is controlled by Jarvis' own config, not by
    this script.
#>

[CmdletBinding()]
param(
    [string]$ProjectDir  = 'C:\Users\serj\Jarvis',
    [string]$OllamaApp   = 'C:\Users\serj\AppData\Local\Programs\Ollama\ollama app.exe',
    [string]$OllamaApi   = 'http://127.0.0.1:11434',
    # Empty = read llm.model from Jarvis config.json (single source of truth).
    # Pass -Model explicitly only to override for a one-off run.
    [string]$Model       = '',
    [string]$ConfigPath  = (Join-Path $env:APPDATA 'Jarvis\config.json'),
    # Used only if config.json is missing/unreadable or has no llm.model.
    [string]$FallbackModel = 'qwen3:8b',
    [int]   $ReadyTimeoutSec = 30
)

$ErrorActionPreference = 'Stop'

# --- Paths -----------------------------------------------------------------
$VenvActivate = Join-Path $ProjectDir '.venv\Scripts\Activate.ps1'
$LogDir       = Join-Path $ProjectDir 'logs'
$Stamp        = Get-Date -Format 'yyyyMMdd_HHmmss'
$LogFile      = Join-Path $LogDir ("autostart_{0}.log" -f $Stamp)

# --- Logging helper --------------------------------------------------------
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $line = ('{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message)
    # Tee-Object in Windows PowerShell 5.1 writes UTF-16; use UTF-8 explicitly
    # so the log is readable by grep/editors/tools.
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Fail {
    param([string]$Message)
    Write-Log $Message 'ERROR'
    Write-Log 'Autostart aborted.' 'ERROR'
    exit 1
}

Write-Log ("=== Jarvis autostart {0} ===" -f $Stamp)
Write-Log ("ProjectDir = {0}" -f $ProjectDir)
Write-Log ("OllamaApi  = {0}" -f $OllamaApi)

# --- Resolve model: -Model param > config.json llm.model > fallback -------
if ($Model) {
    Write-Log ("Model from -Model parameter: {0}" -f $Model)
} else {
    try {
        $cfg = Get-Content -Path $ConfigPath -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
        $Model = [string]$cfg.llm.model
        if ($Model) {
            Write-Log ("Model from config: {0}" -f $ConfigPath)
        } else {
            $Model = $FallbackModel
            Write-Log ("config.json has no llm.model; using fallback '{0}'." -f $Model) 'WARN'
        }
    } catch {
        $Model = $FallbackModel
        Write-Log ("Cannot read {0} ({1}); using fallback '{2}'." -f $ConfigPath, $_.Exception.Message, $Model) 'WARN'
    }
}
Write-Log ("Model      = {0}" -f $Model)

# --- Sanity checks ---------------------------------------------------------
if (-not (Test-Path $ProjectDir))  { Fail ("Project dir not found: {0}"  -f $ProjectDir) }
if (-not (Test-Path $VenvActivate)){ Fail ("venv activate not found: {0}" -f $VenvActivate) }

# --- Helper: is the Ollama API answering? ----------------------------------
function Test-OllamaReady {
    try {
        $resp = Invoke-RestMethod -Uri ("{0}/api/tags" -f $OllamaApi) -TimeoutSec 3 -ErrorAction Stop
        return $null -ne $resp
    } catch {
        return $false
    }
}

# --- Step 1: ensure Ollama server is up ------------------------------------
if (Test-OllamaReady) {
    Write-Log 'Ollama API already responding; skipping launch.'
} else {
    Write-Log 'Ollama API not responding. Starting Ollama...'

    # Preferred: headless server (ollama.exe serve). It is a console program,
    # so -WindowStyle Hidden really hides it: no window, no focus stealing.
    # The GUI 'ollama app.exe' ignores -WindowStyle, so it is only a fallback.
    $OllamaExe = Join-Path (Split-Path $OllamaApp -Parent) 'ollama.exe'

    if (Test-Path $OllamaExe) {
        $OllamaLog    = Join-Path $LogDir ("ollama_{0}.log"     -f $Stamp)
        $OllamaErrLog = Join-Path $LogDir ("ollama_{0}.err.log" -f $Stamp)
        try {
            $op = Start-Process -FilePath $OllamaExe -ArgumentList 'serve' `
                                -WindowStyle Hidden `
                                -RedirectStandardOutput $OllamaLog `
                                -RedirectStandardError  $OllamaErrLog `
                                -PassThru
            Write-Log ("Launched headless: {0} serve (PID {1}). Logs: {2}" -f $OllamaExe, $op.Id, $OllamaErrLog)
        } catch {
            Fail ("Failed to launch ollama serve: {0}" -f $_.Exception.Message)
        }
    } elseif (Test-Path $OllamaApp) {
        Write-Log ("ollama.exe not found at {0}; falling back to GUI app." -f $OllamaExe) 'WARN'
        try {
            Start-Process -FilePath $OllamaApp
            Write-Log ("Launched GUI: {0}" -f $OllamaApp)
        } catch {
            Fail ("Failed to launch Ollama: {0}" -f $_.Exception.Message)
        }
    } else {
        Fail ("Neither ollama.exe nor ollama app.exe found in {0}" -f (Split-Path $OllamaApp -Parent))
    }
}

# --- Step 2: wait for API readiness (up to $ReadyTimeoutSec) ----------------
Write-Log ("Waiting for Ollama API (timeout {0}s)..." -f $ReadyTimeoutSec)
$deadline = (Get-Date).AddSeconds($ReadyTimeoutSec)
$ready = $false
while ((Get-Date) -lt $deadline) {
    if (Test-OllamaReady) { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    Fail ("Ollama API not ready within {0}s." -f $ReadyTimeoutSec)
}
Write-Log 'Ollama API is ready.'

# --- Step 3: warm up the model ---------------------------------------------
Write-Log ("Warming up model '{0}'..." -f $Model)
try {
    $body = @{ model = $Model; prompt = 'ping'; stream = $false } | ConvertTo-Json
    $null = Invoke-RestMethod -Uri ("{0}/api/generate" -f $OllamaApi) `
                              -Method Post -Body $body -ContentType 'application/json' `
                              -TimeoutSec 120 -ErrorAction Stop
    Write-Log 'Model warm-up complete.'
} catch {
    # Warm-up is best-effort: Jarvis can still load the model lazily.
    Write-Log ("Model warm-up failed (continuing anyway): {0}" -f $_.Exception.Message) 'WARN'
}

# --- Step 4 + 5: activate venv and launch Jarvis ---------------------------
Write-Log 'Activating virtualenv...'
try {
    . $VenvActivate
    Write-Log 'venv activated.'
} catch {
    Fail ("Failed to activate venv: {0}" -f $_.Exception.Message)
}

Set-Location $ProjectDir

# Jarvis is a long-lived GUI process. We launch it as a SEPARATE background
# process (Start-Process) and let the launcher return immediately with exit
# code 0 -- this is what Task Scheduler expects.
#
# IMPORTANT: Jarvis writes its own INFO logs to stderr (e.g. "loading audio
# modules..."). We must NOT treat stderr as a crash and must NOT fold Jarvis'
# output into the autostart log. Jarvis gets its own log file, and the venv
# python.exe is invoked directly so no console window steals focus.
$VenvPython = Join-Path $ProjectDir '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $VenvPython)) {
    # Fall back to python.exe if the windowless launcher is absent.
    $VenvPython = Join-Path $ProjectDir '.venv\Scripts\python.exe'
}
$JarvisLog    = Join-Path $LogDir ("jarvis_{0}.log"     -f $Stamp)
$JarvisErrLog = Join-Path $LogDir ("jarvis_{0}.err.log" -f $Stamp)

Write-Log ("Launching Jarvis in background: {0} -m jarvis" -f $VenvPython)
try {
    $proc = Start-Process -FilePath $VenvPython `
                          -ArgumentList '-m', 'jarvis' `
                          -WorkingDirectory $ProjectDir `
                          -WindowStyle Hidden `
                          -RedirectStandardOutput $JarvisLog `
                          -RedirectStandardError  $JarvisErrLog `
                          -PassThru
    Write-Log ("Jarvis started (PID {0}). Logs: {1}" -f $proc.Id, $JarvisLog)
    Write-Log 'Autostart complete.'
    exit 0
} catch {
    Fail ("Failed to start Jarvis: {0}" -f $_.Exception.Message)
}
