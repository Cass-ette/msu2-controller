#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

finish_with_error() {
    printf '\n%s\n' "$1"
    if [[ -t 0 ]]; then
        read -r -p "按回车结束…" unused_input || true
    fi
    exit 1
}
[[ "$(uname -s)" == "Darwin" ]] || finish_with_error "此启动脚本用于 macOS。Windows 请使用 Start.cmd。"
MAC_ENV_PYTHON="$PWD/.venv-mac/bin/python"
[[ -x "$MAC_ENV_PYTHON" ]] || finish_with_error "请先运行 Setup.command 安装 Mac 环境。"
"$MAC_ENV_PYTHON" -c 'import tkinter, serial, PIL, psutil' || finish_with_error "Mac 环境不完整，请重新运行 Setup.command。"
"$MAC_ENV_PYTHON" -B app.py || finish_with_error "启动失败，请查看上方错误或运行 Diagnose.command。"
