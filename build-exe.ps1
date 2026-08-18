<#
Builds a standalone sy-yapper.exe with PyInstaller.

    .\build-exe.ps1

Produces dist\sy-yapper.exe -- a single file with Python and every dependency
inside. The recipient needs no Python install; they still need the Voicebox app
and VB-Audio Cable.

Settings live in a .env next to the exe, created the first time you press
"Save to .env" in the app.
#>

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "No venv found. Run .\setup.ps1 first."
    exit 1
}

& $python -m pip install --quiet --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { Write-Error "Could not install pyinstaller"; exit 1 }

# --windowed: no console window behind the GUI. gui.py redirects the missing
#   stdout/stderr to devnull so an incidental write cannot crash the app.
# --collect-* : sounddevice and soundfile ship native libraries (portaudio,
#   libsndfile) as package data that the dependency scanner does not see.
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name sy-yapper `
    --collect-all sounddevice `
    --collect-all soundfile `
    --collect-submodules websockets `
    --exclude-module pytest `
    --exclude-module PyInstaller `
    (Join-Path $root "main.py")

if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed"; exit 1 }

$exe = Join-Path $root "dist\sy-yapper.exe"
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $exe ($size MB)"
Write-Host "Send that single file. Voicebox and VB-Cable still need installing separately."
