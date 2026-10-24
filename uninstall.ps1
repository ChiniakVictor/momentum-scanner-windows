# Removes Momentum Scanner (per-user). Settings in %APPDATA%\MomentumScanner are kept unless -Purge.
param([switch]$Purge)
$dest = Join-Path $env:LOCALAPPDATA 'Programs\MomentumScanner'
Get-Process MomentumScanner -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) 'Momentum Scanner.lnk') -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Momentum Scanner.lnk') -ErrorAction SilentlyContinue
Remove-Item 'HKCU:\Software\Classes\AppUserModelId\Victor.MomentumScanner' -Recurse -ErrorAction SilentlyContinue
if ($Purge) { Remove-Item (Join-Path $env:APPDATA 'MomentumScanner') -Recurse -ErrorAction SilentlyContinue }
Set-Location $env:TEMP
Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
Write-Host 'Momentum Scanner removed.'
