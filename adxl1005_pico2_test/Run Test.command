#!/bin/bash
# Open the graphical recorder. No terminal input is required.
set -e
adxl_root="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$adxl_root/.cache/matplotlib"
export MPLCONFIGDIR="$adxl_root/.cache/matplotlib"
export PYTHONUNBUFFERED=1
adxl_python_path="$(command -v python3 || true)"
for adxl_python in "$adxl_root/.venv/bin/python" "$adxl_python_path" \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$adxl_python" ] && "$adxl_python" -c 'import sys,tkinter; assert sys.version_info >= (3,10)' >/dev/null 2>&1; then
        exec "$adxl_python" "$adxl_root/launch_gui.py"
    fi
done
printf 'This recorder needs Python 3.10 or newer with Tkinter. The python.org macOS installer includes it.\n'
read -r -p 'Press Return to close. ' adxl_close
exit 1
