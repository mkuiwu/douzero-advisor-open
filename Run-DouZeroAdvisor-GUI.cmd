@echo off
rem Keep this launcher ASCII-only; cmd.exe is not UTF-8 source safe.
setlocal EnableExtensions

for %%I in ("%~f0") do set "ADVISOR_ROOT=%%~dpI"
set "ADVISOR_ROOT=%ADVISOR_ROOT:"=%"
if "%ADVISOR_ROOT:~-1%"=="\" set "ADVISOR_ROOT=%ADVISOR_ROOT:~0,-1%"
set "ADVISOR_LAUNCHER=%ADVISOR_ROOT%\scripts\launch_electron_desktop.ps1"
set "ADVISOR_ELEVATE=%ADVISOR_ROOT%\scripts\elevate_gui_launcher.ps1"

if /I "%~1"=="--elevated" (
  shift
  goto elevated
)

rem Electron starts Java/Python later; this entry point always requests elevation.
if "%~1"=="" (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ADVISOR_ELEVATE%" -LauncherPath "%~f0"
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ADVISOR_ELEVATE%" -LauncherPath "%~f0" -ForwardArguments %*
)
exit /b %ERRORLEVEL%

:elevated

if not exist "%ADVISOR_LAUNCHER%" (
  echo [ERROR] Electron desktop launcher was not found: %ADVISOR_LAUNCHER% 1>&2
  exit /b 2
)
where powershell.exe >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Windows PowerShell was not found on PATH. 1>&2
  exit /b 2
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ADVISOR_LAUNCHER%" %*
exit /b %ERRORLEVEL%
