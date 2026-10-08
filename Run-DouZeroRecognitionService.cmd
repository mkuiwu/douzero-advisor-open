@echo off
rem 此批处理文件必须保持 CRLF；.gitattributes 已声明 Windows 行尾。
setlocal

rem 识别服务的 stdout/stderr 使用 UTF-8，保证中文诊断日志可被 Java 和控制台正确读取。
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "ADVISOR_ROOT=%~dp0"
set "ADVISOR_PYTHON=%ADVISOR_ROOT%.venv\Scripts\python.exe"
if not exist "%ADVISOR_PYTHON%" (
  echo [ERROR] Python virtual environment not found: %ADVISOR_PYTHON% 1>&2
  exit /b 2
)

if not defined DOUZERO_RECOGNITION_MANIFEST set "DOUZERO_RECOGNITION_MANIFEST=%ADVISOR_ROOT%tests\fixtures\live_capture_manifest.json"
if not exist "%DOUZERO_RECOGNITION_MANIFEST%" (
  echo [ERROR] Recognition manifest not found: %DOUZERO_RECOGNITION_MANIFEST% 1>&2
  exit /b 2
)

set "PYTHONPATH=%ADVISOR_ROOT%src;%ADVISOR_ROOT%vendor\DouZero"
"%ADVISOR_PYTHON%" -m douzero_advisor.recognition_service --calibration-manifest "%DOUZERO_RECOGNITION_MANIFEST%" %*
exit /b %ERRORLEVEL%
