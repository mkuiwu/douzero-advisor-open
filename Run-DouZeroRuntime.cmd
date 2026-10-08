@echo off
rem Keep this launcher ASCII-only; cmd.exe is not UTF-8 source safe.
setlocal EnableExtensions EnableDelayedExpansion

rem Use UTF-8 for console, Java, and Python output.
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

for %%I in ("%~f0") do set "ADVISOR_ROOT=%%~dpI"
set "ADVISOR_ROOT=%ADVISOR_ROOT:"=%"
if "%ADVISOR_ROOT:~-1%"=="\" set "ADVISOR_ROOT=%ADVISOR_ROOT:~0,-1%"
set "ADVISOR_JAR=%ADVISOR_ROOT%\java-runtime\target\douzero-runtime-0.1.0-SNAPSHOT.jar"
set "ADVISOR_PREPARE=%ADVISOR_ROOT%\scripts\prepare_startup_workspace.ps1"
set "ADVISOR_SKIP_BUILD=false"
set "ADVISOR_PROFILE=live-runtime"
set "ADVISOR_SKIP_TESTS=true"
rem DEBUG remains the default; a later command-line option can override it.
set "ADVISOR_JAVA_ARGS=--douzero.logging.level=DEBUG"

:parse_args
if "%~1"=="" goto args_parsed
set "ARG=%~1"
if /I "%ARG%"=="--skip-build" (
  set "ADVISOR_SKIP_BUILD=true"
  shift
  goto parse_args
)
if /I "%ARG%"=="--services-only" (
  set "ADVISOR_PROFILE=python-services"
  shift
  goto parse_args
)
if /I "%ARG%"=="--with-tests" (
  set "ADVISOR_SKIP_TESTS=false"
  shift
  goto parse_args
)
rem Restore a --key value pair split by cmd.exe into --key=value.
echo !ARG! | findstr /B /C:"--" >nul
if not errorlevel 1 (
  echo !ARG! | findstr /C:"=" >nul
  if errorlevel 1 (
    if not "%~2"=="" (
      echo %~2 | findstr /B /C:"--" >nul
      if errorlevel 1 (
        set "ARG=!ARG!=%~2"
        shift
      )
    )
  )
)
set "ADVISOR_JAVA_ARGS=!ADVISOR_JAVA_ARGS! !ARG!"
shift
goto parse_args

:args_parsed
if not exist "%ADVISOR_PREPARE%" (
  echo [ERROR] Startup workspace preparer was not found: %ADVISOR_PREPARE% 1>&2
  exit /b 2
)
rem Create a run directory only when Java/Python actually starts.
set "DOUZERO_RUN_DIRECTORY="
for /f "usebackq delims=" %%R in (`powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ADVISOR_PREPARE%" -RepositoryRoot "%ADVISOR_ROOT%"`) do set "DOUZERO_RUN_DIRECTORY=%%R"
if not defined DOUZERO_RUN_DIRECTORY (
  echo [ERROR] Unable to prepare the startup data directory. 1>&2
  exit /b 2
)
if not exist "!DOUZERO_RUN_DIRECTORY!\logs" mkdir "!DOUZERO_RUN_DIRECTORY!\logs"
set "ADVISOR_JAVA_ARGS=!ADVISOR_JAVA_ARGS! --douzero.logging.directory=!DOUZERO_RUN_DIRECTORY!\logs"

where java.exe >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Java 17 was not found on PATH. 1>&2
  exit /b 2
)

pushd "%ADVISOR_ROOT%" >nul

if /I not "%ADVISOR_SKIP_BUILD%"=="true" (
  where mvn.cmd >nul 2>&1
  if errorlevel 1 goto maven_not_found
  if /I "%ADVISOR_SKIP_TESTS%"=="true" (
    echo [INFO] Packaging Java runtime without tests...
    call mvn.cmd -pl java-runtime package -DskipTests
  ) else (
    echo [INFO] Packaging Java runtime and running tests...
    call mvn.cmd -pl java-runtime package
  )
  if errorlevel 1 goto build_failed
)

if not exist "%ADVISOR_JAR%" goto jar_not_found

java.exe -Dfile.encoding=UTF-8 -Dsun.jnu.encoding=UTF-8 -jar "%ADVISOR_JAR%" --spring.profiles.active=!ADVISOR_PROFILE! !ADVISOR_JAVA_ARGS!
set "ADVISOR_EXIT_CODE=%ERRORLEVEL%"
popd >nul

exit /b %ADVISOR_EXIT_CODE%

:maven_not_found
popd >nul
echo [ERROR] Maven was not found on PATH. 1>&2
exit /b 2

:build_failed
set "ADVISOR_EXIT_CODE=%ERRORLEVEL%"
popd >nul
echo [ERROR] Java runtime package failed; Python services were not started. 1>&2
exit /b %ADVISOR_EXIT_CODE%

:jar_not_found
popd >nul
echo [ERROR] Java runtime package was not found: %ADVISOR_JAR% 1>&2
echo [HINT] Remove --skip-build or run: mvn.cmd -pl java-runtime package 1>&2
exit /b 2
