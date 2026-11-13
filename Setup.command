#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

pause_at_end() {
    if [[ -t 0 ]]; then
        read -r -p "按回车结束…" unused_input || true
    fi
}
fail() {
    printf '\n%s\n' "$1"
    pause_at_end
    exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "此脚本用于 macOS。Windows 请使用 Start.cmd。"
MAC_PYTHON="${MSU2_PYTHON:-}"
if [[ -z "$MAC_PYTHON" && -x "$PWD/.venv-mac/bin/python" ]]; then
    MAC_PYTHON="$PWD/.venv-mac/bin/python"
fi
if [[ -z "$MAC_PYTHON" ]]; then
    for candidate in python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 "$HOME/miniconda3/bin/python3"; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
            MAC_PYTHON="$candidate"
            break
        fi
    done
fi
command -v "$MAC_PYTHON" >/dev/null 2>&1 || fail "请先从 https://www.python.org/downloads/macos/ 安装 Python 3.11 或更新版本。"
"$MAC_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || fail "需要 Python 3.11 或更新版本。可通过 MSU2_PYTHON 指定 Python 路径。"
"$MAC_PYTHON" -c 'import tkinter' || fail "当前 Python 缺少 Tkinter。建议安装 python.org 提供的 macOS 安装包，再重试。"

if [[ "$MAC_PYTHON" != "$PWD/.venv-mac/bin/python" ]]; then
    printf '%s\n' "正在创建 Mac 专用环境 .venv-mac …"
    "$MAC_PYTHON" -m venv .venv-mac || fail "虚拟环境创建失败，请查看上方错误。"
fi
MAC_ENV_PYTHON="$PWD/.venv-mac/bin/python"
"$MAC_ENV_PYTHON" -m pip install --disable-pip-version-check --timeout 30 --retries 1 -r requirements.txt || fail "依赖安装失败。请检查网络；若是证书错误，请运行 Python 安装目录里的 Install Certificates.command 后重试。"
"$MAC_ENV_PYTHON" -c 'import tkinter, serial, PIL, psutil' || fail "依赖检查失败，请查看上方错误。"

printf '\n%s\n' "安装完成。连接小屏后运行 Start.command，选择 /dev/cu.usbmodem… 并点击“连接屏幕”。"
printf '%s\n' "如果没有看到小屏，请运行 Diagnose.command。"
pause_at_end
