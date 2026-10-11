param([string]$Wheel,[string]$Sha256,[string]$Version='1.9.0rc1',[string]$InstallRoot=(Join-Path $env:LOCALAPPDATA 'RFAtlasApp'))
& (Join-Path $PSScriptRoot 'install.ps1') -Wheel $Wheel -Sha256 $Sha256 -Version $Version -InstallRoot $InstallRoot
