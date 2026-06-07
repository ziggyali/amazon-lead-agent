$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvDir = Join-Path $RootDir ".venv"

if (-not (Test-Path $VenvDir)) {
    python -m venv $VenvDir
}

$Activate = Join-Path $VenvDir "Scripts\Activate.ps1"
. $Activate

python -m pip install --upgrade pip
python -m pip install -r (Join-Path $RootDir "requirements.txt")

New-Item -ItemType Directory -Force -Path (Join-Path $RootDir "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $RootDir "logs") | Out-Null

Write-Host "Local environment ready."
Write-Host "Edit $RootDir\.env and $RootDir\config.yaml before running the campaign."

