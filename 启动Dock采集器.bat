@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Dock采集器

cd /d "%~dp0"
set "DOCK_ROOT=%CD%"
set "DOCK_VENV=%DOCK_ROOT%\.venv-windows"
set "PLAYWRIGHT_BROWSERS_PATH=%DOCK_ROOT%\.browsers-windows"
set "PYTHONPYCACHEPREFIX=%TEMP%\dock-collector-pycache"

echo ========================================
echo              Dock采集器
echo ========================================
echo.

if exist "%DOCK_VENV%\Scripts\python.exe" goto environment_ready

echo [首次运行] 正在创建 Windows 运行环境……
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv "%DOCK_VENV%"
) else (
    where python >nul 2>nul
    if errorlevel 1 goto python_missing
    python -m venv "%DOCK_VENV%"
)
if errorlevel 1 goto setup_failed

:environment_ready
set "DOCK_PYTHON=%DOCK_VENV%\Scripts\python.exe"

if exist "%DOCK_VENV%\.dock_dependencies_ready" goto dependencies_ready
echo [首次运行] 正在安装程序依赖，请保持网络连接……
"%DOCK_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto setup_failed
"%DOCK_PYTHON%" -m pip install -r "%DOCK_ROOT%\requirements.txt"
if errorlevel 1 goto setup_failed
echo ready>"%DOCK_VENV%\.dock_dependencies_ready"

:dependencies_ready
if exist "%DOCK_VENV%\.dock_browser_ready" goto browser_ready
echo [首次运行] 正在安装程序内置 Chromium……
"%DOCK_PYTHON%" -m playwright install chromium
if errorlevel 1 goto setup_failed
echo ready>"%DOCK_VENV%\.dock_browser_ready"

:browser_ready
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:3780/api/targets ^| Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 (
    echo Dock采集器已经运行，正在打开控制台……
    start "" "http://127.0.0.1:3780"
    goto end
)

echo 正在启动本地控制台……
echo 地址：http://127.0.0.1:3780
echo 停止：在此窗口按 Ctrl+C
echo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for ($i=0; $i -lt 60; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:3780/api/targets ^| Out-Null; Start-Process 'http://127.0.0.1:3780'; break } catch { Start-Sleep -Seconds 1 } }"
"%DOCK_PYTHON%" "%DOCK_ROOT%\run.py"
echo.
echo Dock采集器已停止。
goto pause_end

:python_missing
echo 未检测到 Python 3。
echo 请先从 https://www.python.org/downloads/windows/ 安装 Python 3.10 或更高版本。
echo 安装时请勾选 Add Python to PATH，然后重新双击本文件。
goto pause_end

:setup_failed
echo.
echo Windows 运行环境安装失败，请检查网络和上方错误信息后重试。
goto pause_end

:pause_end
echo.
pause

:end
endlocal
