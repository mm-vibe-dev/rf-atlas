param(
    [string]$Wheel,
    [string]$Sha256,
    [string]$Version = '1.9.0rc1',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'RFAtlasApp'),
    [switch]$Shortcuts
)
$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^\d+\.\d+\.\d+(rc\d+)?$') { throw 'Unsupported version' }
$appRoot = [IO.Path]::GetFullPath($InstallRoot)
$userRoot = [IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\') + '\'
if (-not $appRoot.StartsWith($userRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Choose a directory within your user profile' }
if (Test-Path -LiteralPath $appRoot) {
    if (-not (Test-Path -LiteralPath (Join-Path $appRoot '.rf-atlas-app'))) { throw 'Existing directory is not an RF Atlas app installation' }
}
$downloadRoot = Join-Path ([IO.Path]::GetTempPath()) ('rf-atlas-install-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $downloadRoot | Out-Null
try {
    if (-not $Wheel) {
        $base = "https://github.com/mm-vibe-dev/rf-atlas/releases/download/v$Version"
        $name = "rf_atlas-$Version-py3-none-any.whl"
        $Wheel = Join-Path $downloadRoot $name
        Invoke-WebRequest "$base/$name" -OutFile $Wheel
        $sums = (Invoke-WebRequest "$base/SHA256SUMS").Content
        $matchesForWheel = @($sums -split "`n" | Where-Object { $_ -match ('^[a-fA-F0-9]{64}\s+\*?' + [regex]::Escape($name) + '\s*$') })
        if ($matchesForWheel.Count -ne 1) { throw 'Release checksum manifest is missing or ambiguous' }
        $Sha256 = ($matchesForWheel[0] -split '\s+')[0]
    }
    if ($Sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'A SHA256 is mandatory for a local wheel' }
    if ((Get-FileHash -LiteralPath $Wheel -Algorithm SHA256).Hash -ne $Sha256) { throw 'Wheel checksum mismatch' }
    if ([IO.Path]::GetFileName($Wheel) -ne "rf_atlas-$Version-py3-none-any.whl") { throw 'Wheel version and selected version differ' }
    New-Item -ItemType Directory -Force -Path $appRoot | Out-Null
    Set-Content -LiteralPath (Join-Path $appRoot '.rf-atlas-app') -Value 'RF Atlas user application; runtime data is separate'
    $slot = 'venv-' + $Version + '-' + [guid]::NewGuid().ToString('N')
    $venv = Join-Path $appRoot $slot
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv $venv }
    else { & python -m venv $venv }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ and venv are required' }
    $python = Join-Path $venv 'Scripts\python.exe'
    & $python -m pip install $Wheel
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed; previous installation remains selected' }
    $actual = & (Join-Path $venv 'Scripts\rf-atlas.exe') --version
    if ($LASTEXITCODE -ne 0 -or $actual.Trim() -ne $Version) { throw 'Installed application version check failed' }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'launch.ps1') -Destination (Join-Path $appRoot 'launch.ps1') -Force
    Set-Content -LiteralPath (Join-Path $appRoot 'active.txt') -Value $slot
    if ($Shortcuts) {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'RF Atlas.lnk'))
        $shortcut.TargetPath = (Join-Path $venv 'Scripts\rf-atlas.exe')
        $shortcut.Arguments = 'app --open-browser'
        $shortcut.WorkingDirectory = $appRoot
        $shortcut.Save()
    }
    Write-Output "Installed RF Atlas $Version. Launch with $appRoot\launch.ps1. Existing runtime data was preserved."
} finally {
    $resolvedDownload = [IO.Path]::GetFullPath($downloadRoot)
    $temporaryParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolvedDownload.StartsWith($temporaryParent,[StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolvedDownload).StartsWith('rf-atlas-install-')) {
        Remove-Item -LiteralPath $resolvedDownload -Recurse -Force
    }
}
