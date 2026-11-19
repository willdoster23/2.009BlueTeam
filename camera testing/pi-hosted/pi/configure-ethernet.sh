#!/bin/bash
set -euo pipefail

# A fresh installation may have only a generated profile in /run.
# Create a saved profile with a higher priority than the DHCP default.
case "${1:-}" in
    roof) address=192.168.2.4 ;;
    robot) address=192.168.2.3 ;;
    *) printf 'Use roof or robot.\n' >&2; exit 1 ;;
esac

test "$(id -u)" = 0
profile=brick-mapper-eth0
settings=(
    connection.interface-name eth0
    connection.autoconnect yes
    connection.autoconnect-priority 100
    ipv4.method manual
    ipv4.addresses "$address/24"
    ipv4.gateway ''
    ipv4.dns ''
    ipv4.never-default yes
    ipv4.ignore-auto-dns yes
    ipv4.dad-timeout 2000
)
if nmcli -g connection.uuid connection show id "$profile" >/dev/null 2>&1; then
    nmcli connection modify id "$profile" "${settings[@]}"
else
    nmcli connection add type ethernet ifname eth0 con-name "$profile" save yes "${settings[@]}"
fi

uuid="$(nmcli -g connection.uuid connection show id "$profile")"
filename="$(nmcli -t -f UUID,FILENAME connection show | awk -F: -v uuid="$uuid" '$1 == uuid { print $2 }')"
case "$filename" in
    /etc/NetworkManager/system-connections/*) ;;
    *) printf 'The fixed Ethernet profile was not saved to disk.\n' >&2; exit 1 ;;
esac
printf '%s saved for %s/24.\n' "$profile" "$address"
