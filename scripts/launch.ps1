param([string]$InstallRoot = $PSScriptRoot, [switch]$VersionOnly)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot '.rf-atlas-app'))) { throw 'RF Atlas installation marker is missing' }
$slot = (Get-Content -LiteralPath (Join-Path $InstallRoot 'active.txt') -Raw).Trim()
if ($slot -notmatch '^venv-\d+\.\d+\.\d+(rc\d+)?-[a-f0-9]{32}$') { throw 'Invalid application slot' }
$exe = Join-Path (Join-Path $InstallRoot $slot) 'Scripts\rf-atlas.exe'
if ($VersionOnly) { & $exe --version }
else { & $exe app --open-browser }
exit $LASTEXITCODE
