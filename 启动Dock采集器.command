#!/bin/zsh

set -u

COLLECTOR_DIR="${0:A:h}"
cd "$COLLECTOR_DIR" || exit 1

export PYTHONPATH="$COLLECTOR_DIR/.vendor:$COLLECTOR_DIR"
export PLAYWRIGHT_BROWSERS_PATH="$COLLECTOR_DIR/.browsers"
export PYTHONPYCACHEPREFIX="/tmp/rule-collector-pycache"

clear
echo "========================================"
echo "          Dock采集器"
echo "========================================"
echo

if [[ ! -d "$COLLECTOR_DIR/.vendor/playwright" ]]; then
  echo "缺少运行组件，请确认项目的 .vendor 目录完整。"
  echo "按回车键关闭窗口。"
  read -r
  exit 1
fi

if [[ ! -d "$COLLECTOR_DIR/.browsers/chromium-1181" ]]; then
  echo "缺少程序内置 Chromium 浏览器。"
  echo "请先完成浏览器组件安装，然后重新启动。"
  echo "按回车键关闭窗口。"
  read -r
  exit 1
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

python3 "$COLLECTOR_DIR/run.py"
collector_exit=$?

echo
if [[ $collector_exit -ne 0 ]]; then
  echo "Dock采集器启动失败，错误代码：$collector_exit"
else
  echo "Dock采集器已停止。"
fi
echo "按回车键关闭窗口。"
read -r
