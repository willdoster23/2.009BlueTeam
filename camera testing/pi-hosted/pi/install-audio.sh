#!/bin/bash
set -euo pipefail
BLUE_AUDIO_USER="${1:?Supply the robot Pi login username}"
test "$(id -u)" = 0
command -v ffmpeg >/dev/null
test -e /proc/asound/Device
BLUE_CHANNEL_OPTION='-channels 1'
if ffmpeg -hide_banner -h demuxer=alsa 2>&1 | grep -q -- '-ch_layout'; then
    BLUE_CHANNEL_OPTION='-ch_layout mono'
fi
cat > /etc/systemd/system/bridge-usb-audio.service <<EOF
[Unit]
Description=Blue Team USB microphone over Ethernet
After=mediamtx.service sound.target
Wants=mediamtx.service sound.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$BLUE_AUDIO_USER
SupplementaryGroups=audio
ExecStart=/usr/bin/ffmpeg -nostdin -hide_banner -loglevel warning -thread_queue_size 1024 -f alsa -sample_rate 48000 $BLUE_CHANNEL_OPTION -i hw:CARD=Device,DEV=0 -vn -c:a libopus -b:a 64k -application lowdelay -rtsp_transport tcp -f rtsp rtsp://127.0.0.1:8554/usbaudio
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable bridge-usb-audio.service
systemctl restart bridge-usb-audio.service
echo 'USB microphone publisher installed at the usbaudio stream path.'
