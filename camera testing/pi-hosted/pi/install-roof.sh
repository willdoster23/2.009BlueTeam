#!/bin/bash
set -euo pipefail

# Run on the rooftop Pi with eth0 connected only to the inspection LAN.
BLUE_USER="${1:?Supply the rooftop Pi login username}"
BLUE_GROUP="$(id -gn "$BLUE_USER")"
BLUE_SOURCE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
test "$(id -u)" = 0
test -s /opt/blueteam-network/network.key
test -x /usr/sbin/dnsmasq
ip -o -4 address show dev eth0 | grep -q '192\.168\.2\.4/24 '

install -d -m 755 /opt/brick-mapper/app /etc/brick-mapper
install -m 644 "$BLUE_SOURCE"/app/*.py "$BLUE_SOURCE"/app/*.js "$BLUE_SOURCE"/app/*.html /opt/brick-mapper/app/

cat > /etc/systemd/system/brick-mapper.socket <<'EOF'
[Unit]
Description=Brick Mapper web port on the inspection Ethernet LAN

[Socket]
ListenStream=192.168.2.4:80
BindToDevice=eth0
FreeBind=yes

[Install]
WantedBy=sockets.target
EOF

cat > /etc/systemd/system/brick-mapper.service <<EOF
[Unit]
Description=Blue Team Brick Mapper dashboard
Requires=brick-mapper.socket
After=brick-mapper.socket blueteam-network.service

[Service]
User=$BLUE_USER
Group=$BLUE_GROUP
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 /opt/brick-mapper/app/server.py
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
EOF

cat > /etc/brick-mapper/dnsmasq.conf <<'EOF'
# DHCP only. Normal internet routing and DNS stay with the laptop's Wi-Fi.
interface=eth0
except-interface=lo
bind-dynamic
port=0
dhcp-range=192.168.2.100,192.168.2.199,255.255.255.0,12h
dhcp-option=option:router
dhcp-option=option:dns-server
dhcp-leasefile=/var/lib/misc/brick-mapper.leases
EOF
/usr/sbin/dnsmasq --test --conf-file=/etc/brick-mapper/dnsmasq.conf

cat > /etc/systemd/system/brick-mapper-dhcp.service <<'EOF'
[Unit]
Description=Automatic addresses for the private inspection Ethernet LAN
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file=/etc/brick-mapper/dnsmasq.conf
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

if ! test -f /etc/brick-mapper/avahi-before.conf; then
    cp /etc/avahi/avahi-daemon.conf /etc/brick-mapper/avahi-before.conf
fi
awk '/^\[server\]$/ {print; print "host-name=brick-mapper\nallow-interfaces=eth0\nuse-ipv6=no"; next}
     /^[[:space:]]*(host-name|allow-interfaces|use-ipv6)=/ {next} {print}' \
    /etc/avahi/avahi-daemon.conf > /etc/brick-mapper/avahi.conf
install -m 644 /etc/brick-mapper/avahi.conf /etc/avahi/avahi-daemon.conf
# Remove the static alias if an earlier install used it. Avahi now publishes its own name.
sed -i '/^192\.168\.2\.4[[:space:]]\+brick-mapper\.local[[:space:]]*$/d' /etc/avahi/hosts
systemctl enable avahi-daemon.service
systemctl restart avahi-daemon.service
systemctl daemon-reload
systemctl enable --now brick-mapper.socket brick-mapper-dhcp.service
if systemctl is-active --quiet brick-mapper.service; then
    systemctl restart brick-mapper.service
fi
echo 'Brick Mapper is available at http://brick-mapper.local/'
