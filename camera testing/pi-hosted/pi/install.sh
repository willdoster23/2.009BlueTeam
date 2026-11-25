#!/bin/bash
set -euo pipefail

# Run once on each Pi with its role. Ordinary operation needs no SSH session.
BLUE_ROLE="${1:?Use roof or robot}"
BLUE_USER="${2:?Supply the Pi login username}"
BLUE_SOURCE="$(cd -- "$(dirname -- "$0")" && pwd)"
BLUE_WINCH_OPTION=''
BLUE_PYTHON='/usr/bin/python3 -I'
case "$BLUE_ROLE" in
    roof)
        BLUE_ADDRESS=192.168.2.4
        BLUE_WINCH_OPTION=' --live-winch'
        BLUE_PYTHON='/usr/bin/python3 -E -s'
        ;;
    robot) BLUE_ADDRESS=192.168.2.3 ;;
    *) echo 'Unknown device role' >&2; exit 1 ;;
esac
case "$BLUE_ROLE:${3:-}" in
    roof:|roof:--live-winch) ;;
    roof:--live-winch-up) BLUE_WINCH_OPTION=' --live-winch-up' ;;
    robot:) ;;
    *) echo 'Winch options apply to the rooftop controller only' >&2; exit 1 ;;
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
bash "$BLUE_SOURCE/configure-ethernet.sh" "$BLUE_ROLE"

install -d -m 755 /opt/blueteam-network
install -m 644 "$BLUE_SOURCE/agent.py" /opt/blueteam-network/agent.py
if [[ -n "$BLUE_WINCH_OPTION" ]]; then
    /usr/bin/python3 -c 'import gpiozero'
    install -m 644 "$BLUE_SOURCE"/{winch.py,x_axis.py,outrigger.py,stepper.py} /opt/blueteam-network/
fi
install -o root -g "$BLUE_GROUP" -m 640 "$BLUE_SOURCE/network.key" /opt/blueteam-network/network.key
cat > /etc/systemd/system/blueteam-network.service <<EOF
[Unit]
Description=Blue Team Ethernet controller ($BLUE_ROLE)
After=NetworkManager.service
Wants=NetworkManager.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=$BLUE_USER
Group=$BLUE_GROUP
ExecStart=$BLUE_PYTHON /opt/blueteam-network/agent.py --role $BLUE_ROLE --token-file /opt/blueteam-network/network.key$BLUE_WINCH_OPTION
Restart=always
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
RuntimeDirectory=blueteam-network
WorkingDirectory=/run/blueteam-network

[Install]
WantedBy=multi-user.target
EOF
bash "$BLUE_SOURCE/install-poweroff.sh" "$BLUE_ROLE" "$BLUE_USER"
systemctl daemon-reload
systemctl enable blueteam-network.service
systemctl restart blueteam-network.service
echo "$BLUE_ROLE controller installed at $BLUE_ADDRESS."
