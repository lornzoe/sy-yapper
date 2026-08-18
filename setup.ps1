<#
Creates a .venv and installs requirements.txt into it.

Run once from this directory:
    .\setup.ps1

Afterwards, run the bot with the venv's Python directly:
    .venv\Scripts\python main.py
#>

$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param([string]$Description)
    if ($LASTEXITCODE -ne 0) {
        Write-Error "$Description failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
}

$root = $PSScriptRoot
$venvDir = Join-Path $root ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if (Test-Path $venvPython) {
    Write-Host "Reusing existing venv at $venvDir"
} else {
    $pythonCmd = $null
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $pythonCmd = "python"
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $pythonCmd = "py"
    } else {
        Write-Error "Python not found on PATH. Install Python 3.10+ and try again."
        exit 1
    }

    Write-Host "Creating venv at $venvDir ..."
    & $pythonCmd -m venv $venvDir
    Invoke-Checked "venv creation"
}

Write-Host "Upgrading pip ..."
& $venvPython -m pip install --upgrade pip
Invoke-Checked "pip upgrade"

Write-Host "Installing requirements.txt ..."
& $venvPython -m pip install -r (Join-Path $root "requirements.txt")
Invoke-Checked "dependency install"

$envFile = Join-Path $root ".env"
$envExample = Join-Path $root ".env.example"
if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Host "Created .env from .env.example"
    }
} else {
    Write-Host "Keeping your existing .env"
}

Write-Host ""
Write-Host "Done. Start the control panel with:"
Write-Host "    .venv\Scripts\python main.py"
Write-Host ""
Write-Host "Make sure the Voicebox app is running first, and set your channel"
Write-Host "on the Twitch tab. See README.md for full setup steps."
