# Builds dist\MomentumScanner\MomentumScanner.exe (self-contained, no Python needed to run).
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path .venv)) { & $py -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt
.\.venv\Scripts\python.exe make_icon.py
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name MomentumScanner `
    --icon momentum.ico --add-data "momentum.ico;." --collect-data tzdata `
    --exclude-module tkinter run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built: $PSScriptRoot\dist\MomentumScanner\MomentumScanner.exe"
