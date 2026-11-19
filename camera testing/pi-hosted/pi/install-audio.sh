#!/bin/bash
set -euo pipefail
BLUE_AUDIO_USER="${1:?Supply the robot Pi login username}"
BLUE_AUDIO_CARD="${2:-}"
test "$(id -u)" = 0
command -v ffmpeg >/dev/null
if [[ -z "$BLUE_AUDIO_CARD" ]]; then
    for capture in /proc/asound/card*/pcm0c; do
        if [[ -d "$capture" && -f "$capture/../usbid" ]]; then
            BLUE_AUDIO_CARD="$(cat "$capture/../id")"
            break
        fi
    done
fi
if [[ ! "$BLUE_AUDIO_CARD" =~ ^[A-Za-z0-9_]+$ || ! -d "/proc/asound/$BLUE_AUDIO_CARD/pcm0c" ]]; then
    echo 'Connect a USB microphone or specify its ALSA capture card as the second argument.' >&2
    exit 1
fi
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
ExecStart=/usr/bin/ffmpeg -nostdin -hide_banner -loglevel warning -thread_queue_size 1024 -f alsa -sample_rate 48000 $BLUE_CHANNEL_OPTION -i plughw:CARD=$BLUE_AUDIO_CARD,DEV=0 -vn -c:a libopus -b:a 64k -application lowdelay -rtsp_transport tcp -f rtsp rtsp://127.0.0.1:8554/usbaudio
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable bridge-usb-audio.service
systemctl restart bridge-usb-audio.service
echo 'USB microphone publisher installed at the usbaudio stream path.'
