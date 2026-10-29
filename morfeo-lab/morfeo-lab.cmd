@echo off
rem Windows launcher (cmd and PowerShell): .\morfeo-lab.cmd <command>
rem It does not depend on the PowerShell execution policy and works from any folder.
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PYTHONUTF8=1"
set "MORFEO_LAUNCHER=wrapper"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 -m morfeo_lab %*
) else (
  python -m morfeo_lab %*
)
exit /b %ERRORLEVEL%
