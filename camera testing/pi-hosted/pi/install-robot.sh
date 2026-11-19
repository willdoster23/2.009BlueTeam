#!/bin/bash
set -euo pipefail
username="${1:?Supply the robot Pi username}"
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
group="$(id -gn "$username")"
test "$(id -u)" = 0
test -x /usr/bin/ffmpeg
test -s "$source_dir/network.key"
install -d -m 755 /opt/blueteam-network /etc/mediamtx
install -m 644 "$source_dir"/{agent.py,camera.py,tap.py,tap_audio.py} /opt/blueteam-network/
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
RuntimeDirectory=brick-camera
RuntimeDirectoryMode=0750
RuntimeDirectoryPreserve=restart
ExecStart=/usr/bin/python3 /opt/blueteam-network/camera.py "$camera"
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/blueteam-network.service <<EOF
[Unit]
Description=Robot Ethernet controller and tap output
After=NetworkManager.service bridge-usb-camera.service
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
ReadWritePaths=-/run/brick-camera
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
bash "$source_dir/configure-ethernet.sh" robot
bash "$source_dir/install-poweroff.sh" robot "$username"
systemctl daemon-reload
systemctl enable blueteam-network.service mediamtx.service bridge-usb-camera.service
for usb_card in /proc/asound/card*/usbid; do
    if test -d "$(dirname "$usb_card")/pcm0c"; then
        bash "$source_dir/install-audio.sh" "$username"
        break
    fi
done
echo 'Robot configured. Reboot to activate its address and GPIO18 PWM.'
