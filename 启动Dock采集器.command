#!/bin/zsh

set -u

COLLECTOR_DIR="${0:A:h}"
cd "$COLLECTOR_DIR" || exit 1

VENV_DIR="$COLLECTOR_DIR/.venv-macos"
export PLAYWRIGHT_BROWSERS_PATH="$COLLECTOR_DIR/.browsers"
export PYTHONPYCACHEPREFIX="/tmp/rule-collector-pycache"
export DOCK_USE_VENDOR=0

clear
echo "========================================"
echo "          Dock采集器"
echo "========================================"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "未检测到 Python 3。"
  echo "请先从 https://www.python.org/downloads/macos/ 安装 Python 3.10 或更高版本。"
  echo "安装完成后重新双击本文件。"
  echo "按回车键关闭窗口。"
  read -r
  exit 1
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "[首次运行] 正在创建独立的 macOS 运行环境……"
  if ! python3 -m venv "$VENV_DIR"; then
    echo
    echo "无法创建 Python 虚拟环境，请安装 python.org 提供的 Python 3.10 或更高版本。"
    echo "按回车键关闭窗口。"
    read -r
    exit 1
  fi
fi

DOCK_PYTHON="$VENV_DIR/bin/python"

if ! "$DOCK_PYTHON" -c "import fastapi, uvicorn, playwright, bs4, openpyxl, ddddocr, PIL" >/dev/null 2>&1; then
  echo "[首次运行] 正在安装 Dock采集器运行组件……"
  echo "此过程需要联网，可能需要几分钟。"
  if ! "$DOCK_PYTHON" -m pip install --upgrade pip setuptools wheel; then
    echo
    echo "pip 更新失败，请检查网络连接和上方错误信息。"
    echo "按回车键关闭窗口。"
    read -r
    exit 1
  fi
  if ! "$DOCK_PYTHON" -m pip install --upgrade -r "$COLLECTOR_DIR/requirements.txt"; then
    echo
    echo "运行组件安装失败，请检查网络连接和上方错误信息。"
    echo "按回车键关闭窗口。"
    read -r
    exit 1
  fi
  echo "运行组件安装完成。"
  echo
fi

if ! find "$COLLECTOR_DIR/.browsers" -maxdepth 1 -type d -name 'chromium-*' -print -quit 2>/dev/null | grep -q .; then
  echo "[首次运行] 正在安装程序内置 Chromium……"
  echo "浏览器文件较大，请保持网络连接。"
  mkdir -p "$COLLECTOR_DIR/.browsers"
  if ! "$DOCK_PYTHON" -m playwright install chromium; then
    echo
    echo "内置 Chromium 安装失败，请检查网络连接和上方错误信息。"
    echo "按回车键关闭窗口。"
    read -r
    exit 1
  fi
  echo "内置 Chromium 安装完成。"
  echo
fi

if curl -fsS --max-time 1 "http://127.0.0.1:3780/api/targets" >/dev/null 2>&1; then
  echo "Dock采集器已经运行，正在打开控制台……"
  open "http://127.0.0.1:3780"
  echo "可以关闭此窗口。"
  sleep 2
  exit 0
fi

echo "正在启动本地控制台……"
echo "地址：http://127.0.0.1:3780"
echo "停止：在此窗口按 Control+C"
echo

(
  for attempt in {1..30}; do
    if curl -fsS --max-time 1 "http://127.0.0.1:3780/api/targets" >/dev/null 2>&1; then
      open "http://127.0.0.1:3780"
      exit 0
    fi
    sleep 1
  done
) &

"$DOCK_PYTHON" "$COLLECTOR_DIR/run.py"
collector_exit=$?

echo
if [[ $collector_exit -ne 0 ]]; then
  echo "Dock采集器启动失败，错误代码：$collector_exit"
else
  echo "Dock采集器已停止。"
fi
echo "按回车键关闭窗口。"
read -r
