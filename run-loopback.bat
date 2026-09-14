@echo off
rem 抓系统声音跑（开发环境命令行方式）。
rem 打包版请直接双击 dist\livecap\livecap.exe。
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [!] 还没装环境，先运行: powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
  exit /b 1
)

".venv\Scripts\python.exe" -m livecap.main --source loopback %*
