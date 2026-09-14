@echo off
rem livecap - 日文直播实时字幕（开发环境启动方式）
rem 打包版请直接用 dist\livecap\livecap.exe，双击即可。
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [!] 还没装环境，先运行: powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
  exit /b 1
)

rem 不带参数 = 抓系统声音（默认）；也支持 --url / --source file 等参数
".venv\Scripts\python.exe" -m livecap.gui %*
