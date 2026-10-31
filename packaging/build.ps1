# Builds dist\rClone GUI\ and, if Inno Setup 6 is installed, the installer.
# Run from anywhere, with the dependencies from requirements-desktop.txt in .venv
# or the active Python environment.
param([string]$Version = "0.0.0")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

& $python packaging\fetch_rclone.py
if ($LASTEXITCODE -ne 0) { throw "Downloading rclone failed" }

& $python -m PyInstaller packaging\rclone-gui.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    foreach ($candidate in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )) {
        if (Test-Path $candidate) { $iscc = $candidate; break }
    }
}
if ($iscc) {
    & $iscc "/DAppVersion=$Version" packaging\rclone-gui.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
} else {
    Write-Warning "Inno Setup 6 not found; skipping the installer"
}
