#!/bin/bash
# Double-click in Finder. Always use this checkout's current source files.
updater_root="$(cd "$(dirname "$0")" && pwd)"
updater_python="$(command -v python3 || true)"
for candidate in "$updater_python" /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys; assert sys.version_info >= (3,10)' >/dev/null 2>&1; then
        "$candidate" "$updater_root/tools/update_pis.py"
        updater_status=$?
        printf '\n'
        read -r -p 'Press Return to close. ' updater_close
        exit "$updater_status"
    fi
done
printf 'Python 3.10 or newer is needed to run the updater.\n'
read -r -p 'Press Return to close. ' updater_close
exit 1
