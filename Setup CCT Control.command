#!/bin/sh
task_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sh "$task_root/scripts/setup.sh"
task_result=$?
if [ "$task_result" -ne 0 ]; then
    printf '%s\n' 'Setup failed. Read the message above for the next step.'
fi
printf '%s' 'Press Return to close this window. '
read -r task_reply
exit "$task_result"
