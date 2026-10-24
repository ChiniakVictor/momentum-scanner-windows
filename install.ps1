# Per-user install (no admin): %LOCALAPPDATA%\Programs\MomentumScanner + Start Menu and Desktop shortcuts.
param([string]$Source = "$PSScriptRoot\dist\MomentumScanner")
$ErrorActionPreference = 'Stop'
$dest = Join-Path $env:LOCALAPPDATA 'Programs\MomentumScanner'
Get-Process MomentumScanner -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$dest*" } | Stop-Process -Force
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item "$Source\*" $dest -Recurse -Force
Copy-Item "$PSScriptRoot\uninstall.ps1" $dest -Force
$exe = Join-Path $dest 'MomentumScanner.exe'
$ico = Join-Path $dest '_internal\momentum.ico'
$ws = New-Object -ComObject WScript.Shell
foreach ($lnk in @((Join-Path ([Environment]::GetFolderPath('Programs')) 'Momentum Scanner.lnk'),
                   (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Momentum Scanner.lnk'))) {
    $s = $ws.CreateShortcut($lnk); $s.TargetPath = $exe; $s.WorkingDirectory = $dest
    if (Test-Path $ico) { $s.IconLocation = $ico } ; $s.Description = 'Momentum Scanner'; $s.Save()
}
Write-Host "Installed to $dest"
