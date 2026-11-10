#!/bin/zsh
BRICK_CONTROL_DIR="${0:A:h}"
BRICK_ACTION="$1"
BRICK_RESULT=1
for BRICK_PYTHON in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [[ -x "$BRICK_PYTHON" ]] && "$BRICK_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))' 2>/dev/null; then
    if [[ "$BRICK_MAP_NO_BROWSER" == "1" ]]; then
      "$BRICK_PYTHON" "$BRICK_CONTROL_DIR/control.py" "$BRICK_ACTION" --no-browser
    else
      "$BRICK_PYTHON" "$BRICK_CONTROL_DIR/control.py" "$BRICK_ACTION"
    fi
    BRICK_RESULT=$?
    break
  fi
done
if [[ "$BRICK_RESULT" != "0" ]]; then
  print "If Python was not found, install Python 3.9 or newer from python.org."
fi
if [[ -t 0 ]]; then
  read "BRICK_REPLY?Press Return to finish. "
fi
exit "$BRICK_RESULT"
