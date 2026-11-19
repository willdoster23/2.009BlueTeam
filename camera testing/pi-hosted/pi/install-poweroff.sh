#!/bin/bash
set -euo pipefail

role="${1:?Use roof or robot}"
username="${2:?Supply the controller service username}"
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
case "$role" in
    robot) delay=4 ;;
    roof) delay=10 ;;
    *) exit 1 ;;
esac
test "$(id -u)" = 0
[[ "$username" =~ ^[a-z_][a-z0-9_-]*$ ]]
id "$username" >/dev/null
test -x /usr/lib/polkit-1/polkitd || test -x /usr/libexec/polkitd
umask 022

cat > /etc/systemd/system/brick-mapper-poweroff.service <<'EOF'
[Unit]
Description=Shut down this Raspberry Pi from Brick Mapper

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl --no-block poweroff
EOF

cat > /etc/systemd/system/brick-mapper-poweroff.timer <<EOF
[Unit]
Description=Allow Brick Mapper to acknowledge shutdown before this Pi powers off

[Timer]
OnActiveSec=${delay}s
AccuracySec=100ms
EOF

install -d -m 755 /etc/polkit-1/rules.d
cat > /etc/polkit-1/rules.d/49-brick-mapper-poweroff.rules <<EOF
polkit.addRule(function (action, subject) {
    if (action.id === "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") === "brick-mapper-poweroff.timer" &&
        action.lookup("verb") === "start" &&
        subject.user === "$username") {
        return polkit.Result.YES;
    }
});
EOF
chmod 644 /etc/polkit-1/rules.d/49-brick-mapper-poweroff.rules

if [[ "${3:-}" == '--update' ]]; then
    test -f /opt/blueteam-network/agent.py
    install -m 644 "$source_dir/agent.py" /opt/blueteam-network/agent.py
    if [[ "$role" == roof ]]; then
        test -f /opt/brick-mapper/app/server.py
        install -m 644 "$source_dir"/{x_axis.py,winch.py,outrigger.py} /opt/blueteam-network/
        install -m 644 "$source_dir"/../app/{map.html,dashboard.js,network.py,server.py,automatic.js,storage.js,player.js,tap-score.js,tap-analysis.js} /opt/brick-mapper/app/
    fi
    systemctl daemon-reload
    systemctl restart blueteam-network.service
    if [[ "$role" == roof ]] && systemctl is-active --quiet brick-mapper.service; then
        systemctl restart brick-mapper.service
    fi
fi
