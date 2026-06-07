$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvDir = Join-Path $RootDir ".venv"
$ConfigFile = Join-Path $RootDir "config.yaml"
$LogDir = Join-Path $RootDir "logs"
$Timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$RunLog = Join-Path $LogDir "$Timestamp-run.log"
$LatestLog = Join-Path $LogDir "latest.log"

if (-not (Test-Path $ConfigFile)) {
    throw "Missing config.yaml. Copy config.example.yaml to config.yaml and configure it first."
}

if (-not (Test-Path $VenvDir)) {
    throw "Missing .venv. Run scripts/install_local.ps1 first."
}

New-Item -ItemType Directory -Force -Path (Join-Path $RootDir "data") | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$RunLabel = "LIVE RUN"
if ($args -contains "--dry-run") {
    $RunLabel = "DRY RUN"
}

if (Test-Path (Join-Path $RootDir ".env")) {
    Get-Content (Join-Path $RootDir ".env") | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $parts = $line.Split("=", 2)
        if ($parts.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
        }
    }
}

. (Join-Path $VenvDir "Scripts\Activate.ps1")

"[$RunLabel] Starting local campaign run at $Timestamp" | Tee-Object -FilePath $RunLog
$ExitCode = 0
& python (Join-Path $RootDir "run_campaign.py") --config $ConfigFile --mode full @args 2>&1 | Tee-Object -FilePath $RunLog -Append
$ExitCode = $LASTEXITCODE
Copy-Item $RunLog $LatestLog -Force
if ($ExitCode -ne 0) {
    throw "Run failed. See $RunLog"
}
Write-Host "Run complete. Log: $RunLog"
