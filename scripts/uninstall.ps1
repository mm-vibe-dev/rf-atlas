param([string]$InstallRoot=(Join-Path $env:LOCALAPPDATA 'RFAtlasApp'))
$ErrorActionPreference='Stop'
$appRoot=[IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$userRoot=[IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\')+'\'
if (-not $appRoot.StartsWith($userRoot,[StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-Path -LiteralPath (Join-Path $appRoot '.rf-atlas-app'))) { throw 'Refusing to remove an unmarked directory outside the user application installation' }
# Reparse points could escape the checked directory. Preserve them for manual review.
if (@(Get-ChildItem -LiteralPath $appRoot -Force -Recurse | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count -or
    ((Get-Item -LiteralPath $appRoot).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Refusing recursive removal through reparse points' }
Remove-Item -LiteralPath $appRoot -Recurse -Force
Write-Output 'RF Atlas application removed. Runtime recordings, catalog and settings were preserved.'
