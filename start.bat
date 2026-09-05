@echo off
REM PPT->Obsidian 智能笔记 启动器 (Windows)
cd /d "%~dp0"
set PY=python
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python, 请先安装 Python 3.9+ 并勾选 Add to PATH
  pause
  exit /b 1
)
echo 正在启动 PPT-^>Obsidian 智能笔记 ...
python run.py
pause
