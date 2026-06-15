#!/usr/bin/env bash
# Blind Glasses — install systemd services so the app starts on boot and
# reports its IP via Telegram after every restart.
#
#   sudo ./install_service.sh
#
# Creates two services:
#   blind-glasses-ip   oneshot — sends the device IP to Telegram once network is up
#   blind-glasses      the main app, restarted automatically if it crashes
#
# Both run as your normal user (not root) so audio (mic + espeak) works.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo:  sudo ./install_service.sh" >&2
    exit 1
fi

# The non-root user who invoked sudo — the app runs as them.
REAL_USER="${SUDO_USER:-}"
if [[ -z "$REAL_USER" || "$REAL_USER" == "root" ]]; then
    echo "Run this via 'sudo ./install_service.sh' from your normal user account," >&2
    echo "not from a root shell — the service must run as a real user for audio." >&2
    exit 1
fi

REAL_UID="$(id -u "$REAL_USER")"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$REPO_DIR/.env/bin/python"

if [[ ! -x "$PY" ]]; then
    echo "Python venv not found at: $PY" >&2
    echo "Create it first:  python3 -m venv .env --system-site-packages" >&2
    exit 1
fi

# Only request hardware groups that actually exist on this system.
SUP_GROUPS=""
for g in gpio i2c spi audio video dialout; do
    if getent group "$g" >/dev/null 2>&1; then
        SUP_GROUPS="$SUP_GROUPS $g"
    fi
done
SUP_GROUPS="${SUP_GROUPS# }"

echo "User         : $REAL_USER (uid $REAL_UID)"
echo "Project dir  : $REPO_DIR"
echo "Python       : $PY"
echo "HW groups    : ${SUP_GROUPS:-<none found>}"
echo

# Linger keeps the user's session (and PipeWire/PulseAudio) alive at boot
# without a login, so the app can use the microphone and speaker.
echo "Enabling user lingering for $REAL_USER ..."
loginctl enable-linger "$REAL_USER"

echo "Writing /etc/systemd/system/blind-glasses-ip.service ..."
cat >/etc/systemd/system/blind-glasses-ip.service <<EOF
[Unit]
Description=Blind Glasses — report IP via Telegram on boot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$REAL_USER
WorkingDirectory=$REPO_DIR
ExecStart="$PY" "$REPO_DIR/report_ip.py"

[Install]
WantedBy=multi-user.target
EOF

echo "Writing /etc/systemd/system/blind-glasses.service ..."
cat >/etc/systemd/system/blind-glasses.service <<EOF
[Unit]
Description=Blind Glasses — main application
After=network-online.target sound.target blind-glasses-ip.service
Wants=network-online.target

[Service]
Type=simple
User=$REAL_USER
SupplementaryGroups=$SUP_GROUPS
WorkingDirectory=$REPO_DIR
Environment=XDG_RUNTIME_DIR=/run/user/$REAL_UID
Environment=PYTHONUNBUFFERED=1
ExecStart="$PY" "$REPO_DIR/main.py"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd and enabling services ..."
systemctl daemon-reload
systemctl enable --now blind-glasses-ip.service
systemctl enable --now blind-glasses.service

echo
echo "Done. Useful commands:"
echo "  systemctl status blind-glasses          # is the app running?"
echo "  journalctl -u blind-glasses -f          # live app logs"
echo "  journalctl -u blind-glasses-ip -b       # the IP message from this boot"
echo "  sudo systemctl restart blind-glasses    # restart the app"
