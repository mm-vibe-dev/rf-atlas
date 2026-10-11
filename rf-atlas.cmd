@echo off
setlocal
if "%~1"=="" (
  call "%~f0" app --open-browser
  exit /b %errorlevel%
)
set "RF_ATLAS_PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "RF_ATLAS_PY=%~dp0.venv\Scripts\python.exe"
if exist "%~dp0config.local.toml" (
  "%RF_ATLAS_PY%" "%~dp0rf_atlas.py" --config "%~dp0config.local.toml" %*
) else (
  "%RF_ATLAS_PY%" "%~dp0rf_atlas.py" %*
)
exit /b %errorlevel%
