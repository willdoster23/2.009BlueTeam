#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
preview_script="$PWD/serve.py"
preview_state="${TMPDIR:-/tmp}/brick-mapper-preview-$(id -u)"
preview_url='http://127.0.0.1:8765/'
umask 077
mkdir -p -- "$preview_state"

if [[ -s "$preview_state/pid" ]]; then
    read -r preview_pid < "$preview_state/pid"
    if [[ "$preview_pid" =~ ^[0-9]+$ ]] &&
        [[ "$(ps -p "$preview_pid" -o command=)" == *" -B $preview_script" ]]; then
        printf 'Preview already running at %s\n' "$preview_url"
        [[ "${BRICK_PREVIEW_BROWSER:-1}" == 0 ]] || open "$preview_url"
        exit 0
    fi
fi

if ! command -v python3 >/dev/null; then
    printf 'Install Python 3 before starting the Brick Mapper preview.\n' >&2
    exit 1
fi
nohup python3 -B "$preview_script" > "$preview_state/server.log" 2>&1 < /dev/null &
preview_pid=$!
printf '%s\n' "$preview_pid" > "$preview_state/pid"
for attempt in {1..30}; do
    if ! kill -0 "$preview_pid" 2>/dev/null; then
        rm -f -- "$preview_state/pid"
        cat "$preview_state/server.log" >&2
        exit 1
    fi
    if curl -fs --noproxy '*' --max-time 1 "${preview_url}api/app" | grep -q '"hostedOn": "preview"'; then
        printf 'Brick Mapper preview is ready at %s\n' "$preview_url"
        [[ "${BRICK_PREVIEW_BROWSER:-1}" == 0 ]] || open "$preview_url"
        exit 0
    fi
    sleep 0.1
done
kill "$preview_pid" 2>/dev/null || true
rm -f -- "$preview_state/pid"
printf 'The preview did not start. See %s/server.log\n' "$preview_state" >&2
exit 1
