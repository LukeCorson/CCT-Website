#!/bin/sh
# First-time setup for macOS and Linux. No Codex installation is required.
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$task_root"
task_python="$task_root/.venv/bin/python"
if [ ! -x "$task_python" ]; then
    if [ -n "${CCT_PYTHON:-}" ]; then
        task_runtime="$CCT_PYTHON"
    elif command -v python3 >/dev/null 2>&1; then
        task_runtime=$(command -v python3)
    elif command -v python >/dev/null 2>&1; then
        task_runtime=$(command -v python)
    else
        printf '%s\n' 'Install Python 3.11 or newer, then run setup again.' >&2
        exit 1
    fi
    "$task_runtime" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required.")'
    "$task_runtime" -m venv "$task_root/.venv"
fi
"$task_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Recreate .venv with Python 3.11 or newer.")'
"$task_python" -m pip install -r "$task_root/requirements.txt"
"$task_python" -c 'import PySide6, pyqtgraph, serial, numpy; print("CCT desktop dependencies are ready.")'
printf '%s\n' 'Setup complete. Run: sh scripts/start.sh'
