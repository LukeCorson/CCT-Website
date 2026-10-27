#!/bin/sh
task_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sh "$task_root/scripts/start.sh" "$@"
task_result=$?
if [ "$task_result" -ne 0 ]; then
    printf '%s\n' 'CCT Control could not start. Read the message above.'
    printf '%s' 'Press Return to close this window. '
    read -r task_reply
fi
exit "$task_result"
