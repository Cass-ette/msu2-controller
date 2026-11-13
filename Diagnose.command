#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
MAC_DIAG_PYTHON="$PWD/.venv-mac/bin/python"
if [[ ! -x "$MAC_DIAG_PYTHON" ]]; then
    MAC_DIAG_PYTHON="${MSU2_PYTHON:-python3}"
fi
if ! command -v "$MAC_DIAG_PYTHON" >/dev/null 2>&1; then
    printf '%s\n' "未找到 Python。请从 https://www.python.org/downloads/macos/ 安装 Python 3.11 或更新版本。"
    exit 1
fi
"$MAC_DIAG_PYTHON" -B doctor.py --probe
if [[ -t 0 ]]; then
    read -r -p "按回车结束…" unused_input || true
fi
