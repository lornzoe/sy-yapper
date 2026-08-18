<#
Builds a zip of this project suitable for sending to someone else.

    .\package.ps1

Deliberately excludes .env (your channel, and any token you may add later),
the .venv, caches, and backups. The recipient gets .env.example and runs
setup.ps1, which turns it into their own .env.
#>

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$name = "sy-yapper"
$staging = Join-Path ([System.IO.Path]::GetTempPath()) "$name-package"
$zip = Join-Path $root "$name.zip"

$include = @(
    "main.py", "gui.py", "gui_forms.py", "bot_runner.py", "config.py",
    "settings_schema.py", "env_file.py", "errors.py", "twitch_chat.py",
    "voicebox_client.py", "audio_player.py", "list_audio_devices.py",
    "fix_hf_symlinks.py", "requirements.txt", "setup.ps1", "run-gui.ps1",
    "README.md", ".env.example"
)

if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging | Out-Null

$missing = @()
foreach ($file in $include) {
    $src = Join-Path $root $file
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $staging $file)
    } else {
        $missing += $file
    }
}

if ($missing.Count -gt 0) {
    Write-Error "Missing expected files: $($missing -join ', ')"
    exit 1
}

# Belt and braces: never ship a real .env even if the list above changes.
Get-ChildItem $staging -Force -Filter ".env" | Remove-Item -Force -ErrorAction SilentlyContinue

if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zip -Force
Remove-Item $staging -Recurse -Force

$size = [math]::Round((Get-Item $zip).Length / 1KB, 1)
Write-Host ""
Write-Host "Built $zip ($size KB, $($include.Count) files)"
Write-Host "Send that file. They unzip it, run .\setup.ps1, then .venv\Scripts\python main.py"
