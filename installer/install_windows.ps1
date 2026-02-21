param(
  [switch]$Launch
)

$ErrorActionPreference = "Stop"
Write-Host "Magic Mask Pro Studio one-click installer" -ForegroundColor Cyan

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
  throw "winget is required on Windows 11. Install App Installer from Microsoft Store first."
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Host "Installing Python 3.11..."
  winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "Installing FFmpeg..."
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
}

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe installer\install_sam2.py

if ($Launch) {
  Write-Host "Starting app at http://localhost:8000"
  .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
} else {
  Write-Host "Install complete. Run: .\\installer\\launch_windows.bat"
}
