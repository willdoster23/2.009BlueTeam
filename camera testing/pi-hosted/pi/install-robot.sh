#!/bin/bash
set -euo pipefail
username="${1:?Supply the robot Pi username}"
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
group="$(id -gn "$username")"
test "$(id -u)" = 0
test -x /usr/bin/ffmpeg
test -s "$source_dir/network.key"
install -d -m 755 /opt/blueteam-network /etc/mediamtx
install -m 644 "$source_dir/agent.py" "$source_dir/tap.py" /opt/blueteam-network/
install -o root -g "$group" -m 640 "$source_dir/network.key" /opt/blueteam-network/network.key
install -m 755 "$source_dir/mediamtx" /usr/local/bin/mediamtx

cat > /etc/mediamtx/mediamtx.yml <<'EOF'
rtsp: yes
rtspAddress: 127.0.0.1:8554
rtspTransports: [tcp]
rtmp: no
hls: no
srt: no
moq: no
webrtc: yes
webrtcAddress: 192.168.2.3:8889
webrtcLocalUDPAddress: 192.168.2.3:8189
webrtcIPsFromInterfaces: no
webrtcAdditionalHosts: [192.168.2.3]
paths:
  usbtest:
  usbaudio:
EOF

cat > /etc/systemd/system/mediamtx.service <<EOF
[Unit]
Description=Robot camera and microphone streaming
After=network.target
StartLimitIntervalSec=0

[Service]
User=$username
ExecStart=/usr/local/bin/mediamtx /etc/mediamtx/mediamtx.yml
Restart=always
RestartSec=2
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

camera="$(find /dev/v4l/by-id -maxdepth 1 -name '*video-index0' -print -quit)"
test -n "$camera"
cat > /etc/systemd/system/bridge-usb-camera.service <<EOF
[Unit]
Description=USB inspection camera
After=mediamtx.service
Wants=mediamtx.service
StartLimitIntervalSec=0

[Service]
User=$username
SupplementaryGroups=video
ExecStart=/usr/bin/ffmpeg -nostdin -hide_banner -loglevel warning -f v4l2 -input_format mjpeg -framerate 30 -video_size 1280x720 -i "$camera" -an -c:v libx264 -pix_fmt yuv420p -preset ultrafast -tune zerolatency -profile:v baseline -b:v 2M -g 30 -bf 0 -rtsp_transport tcp -f rtsp rtsp://127.0.0.1:8554/usbtest
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/blueteam-network.service <<EOF
[Unit]
Description=Robot Ethernet controller and tap output
After=NetworkManager.service
Wants=NetworkManager.service
StartLimitIntervalSec=0

[Service]
User=$username
Group=$group
ExecStartPre=+/usr/bin/python3 /opt/blueteam-network/tap.py setup $username
ExecStart=/usr/bin/python3 /opt/blueteam-network/agent.py --role robot --tap --token-file /opt/blueteam-network/network.key
ExecStopPost=+/usr/bin/python3 /opt/blueteam-network/tap.py reset
Restart=always
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

config=/boot/firmware/config.txt
if ! grep -Fxq '# Blue Team solenoid PWM' "$config"; then
    cp "$config" "$config.blue-backup"
    printf '\n[all]\n# Blue Team solenoid PWM\ndtparam=audio=off\ndtoverlay=pwm,pin=18,func=2\n' >> "$config"
fi
profile="$(nmcli -g GENERAL.CONNECTION device show eth0)"
test -n "$profile"
test "$profile" != --
nmcli connection modify "$profile" connection.autoconnect yes ipv4.method manual ipv4.addresses 192.168.2.3/24 ipv4.gateway '' ipv4.dns '' ipv4.never-default yes ipv4.ignore-auto-dns yes ipv4.dad-timeout 2000
systemctl daemon-reload
systemctl enable blueteam-network.service mediamtx.service bridge-usb-camera.service
if test -e /proc/asound/Device; then
    bash "$source_dir/install-audio.sh" "$username"
fi
echo 'Robot configured. Reboot to activate its address and GPIO18 PWM.'
