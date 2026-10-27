#!/bin/sh
# Start from any working directory. Forward optional --data-dir and other flags.
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$task_root"
task_python="$task_root/.venv/bin/python"
if [ ! -x "$task_python" ]; then
    printf '%s\n' 'CCT Control needs one-time setup. Run: sh scripts/setup.sh' >&2
    exit 1
fi
exec "$task_python" -m cct.app "$@"
