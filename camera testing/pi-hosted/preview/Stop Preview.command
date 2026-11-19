#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
preview_script="$PWD/serve.py"
preview_state="${TMPDIR:-/tmp}/brick-mapper-preview-$(id -u)"
if [[ -s "$preview_state/pid" ]]; then
    read -r preview_pid < "$preview_state/pid"
    if [[ "$preview_pid" =~ ^[0-9]+$ ]] &&
        [[ "$(ps -p "$preview_pid" -o command=)" == *" -B $preview_script" ]]; then
        kill "$preview_pid"
        for attempt in {1..20}; do
            kill -0 "$preview_pid" 2>/dev/null || break
            sleep 0.1
        done
    fi
    rm -f -- "$preview_state/pid"
fi
printf 'Brick Mapper preview is stopped.\n'
