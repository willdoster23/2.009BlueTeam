#!/bin/bash
set -euo pipefail

# Run once on each Pi with its role. Ordinary operation needs no SSH session.
BLUE_ROLE="${1:?Use roof or robot}"
BLUE_USER="${2:?Supply the Pi login username}"
BLUE_SOURCE="$(cd -- "$(dirname -- "$0")" && pwd)"
case "$BLUE_ROLE" in
    roof) BLUE_ADDRESS=192.168.2.4 ;;
    robot) BLUE_ADDRESS=192.168.2.3 ;;
    *) echo 'Unknown device role' >&2; exit 1 ;;
esac
test "$(id -u)" = 0
test -s "$BLUE_SOURCE/network.key"
test -f "$BLUE_SOURCE/agent.py"
BLUE_GROUP="$(id -gn "$BLUE_USER")"
BLUE_CONNECTION="$(nmcli -g GENERAL.CONNECTION device show eth0)"
test -n "$BLUE_CONNECTION"
test "$BLUE_CONNECTION" != --

install -d -m 700 /var/lib/blueteam-network
if ! test -f /var/lib/blueteam-network/ethernet-before.txt; then
    nmcli connection show "$BLUE_CONNECTION" > /var/lib/blueteam-network/ethernet-before.txt
fi
# NetworkManager writes these settings to persistent storage.
nmcli connection modify "$BLUE_CONNECTION" connection.autoconnect yes \
    ipv4.method manual ipv4.addresses "$BLUE_ADDRESS/24" \
    ipv4.gateway '' ipv4.dns '' ipv4.never-default yes ipv4.ignore-auto-dns yes \
    ipv4.dad-timeout 2000

install -d -m 755 /opt/blueteam-network
install -m 644 "$BLUE_SOURCE/agent.py" /opt/blueteam-network/agent.py
install -o root -g "$BLUE_GROUP" -m 640 "$BLUE_SOURCE/network.key" /opt/blueteam-network/network.key
cat > /etc/systemd/system/blueteam-network.service <<EOF
[Unit]
Description=Blue Team Ethernet data and relay simulation ($BLUE_ROLE)
After=NetworkManager.service
Wants=NetworkManager.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=$BLUE_USER
Group=$BLUE_GROUP
ExecStart=/usr/bin/python3 -I /opt/blueteam-network/agent.py --role $BLUE_ROLE --token-file /opt/blueteam-network/network.key
Restart=always
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable blueteam-network.service
systemctl restart blueteam-network.service
echo "$BLUE_ROLE controller installed at $BLUE_ADDRESS. Relay mode is simulation."
