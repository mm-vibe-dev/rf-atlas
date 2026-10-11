# Project-scoped setup. Never installs drivers or changes the receiver runtime.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExecutable = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { & $pythonCommand.Source -3 -m venv (Join-Path $PSScriptRoot '.venv') }
    else {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $pythonCommand) { throw 'Install Python 3.11 or newer, then reopen this installer.' }
        & $pythonCommand.Source -m venv (Join-Path $PSScriptRoot '.venv')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
}
& $pythonExecutable -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11 or newer is required"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
& $pythonExecutable -m pip install '.[plots,analytics]'
if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed. Check network access and try again.' }
$configPath = Join-Path $PSScriptRoot 'config.local.toml'
if (-not (Test-Path -LiteralPath $configPath)) {
    Write-Host 'Select the folder containing your EXISTING compatible rtl_power, rtl_sdr and rtl_test executables.'
    Write-Host 'No driver or DLL will be installed or replaced. Cancel to use tools already on PATH.'
    $toolsFolder = ''
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = 'Existing RTL-SDR tools folder (no drivers will be changed)'
        if ($dialog.ShowDialog() -eq 'OK') { $toolsFolder = $dialog.SelectedPath }
        $dialog.Dispose()
    } catch { $toolsFolder = Read-Host 'Existing tools folder, or Enter for PATH' }
    $configLines = @('[runtime]', "root='.'", '[hardware]', "device='0'", '[tools]')
    foreach ($toolName in @('rtl_power','rtl_sdr','rtl_test')) {
        $toolPath = if ($toolsFolder) { Join-Path $toolsFolder ($toolName+'.exe') } else { $toolName+'.exe' }
        if ($toolsFolder -and -not (Test-Path -LiteralPath $toolPath -PathType Leaf)) { throw "Missing $toolName in the selected folder." }
        $configLines += ($toolName + '=' + (ConvertTo-Json -InputObject $toolPath -Compress))
    }
    [IO.File]::WriteAllLines($configPath, $configLines, (New-Object Text.UTF8Encoding($false)))
}
Write-Host 'Setup complete. Opening RF Atlas. Close its console with Ctrl+C to stop acquisition.'
& (Join-Path $PSScriptRoot 'rf-atlas.cmd')
