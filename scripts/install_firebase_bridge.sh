#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/etr-core"
ENV_FILE="/etc/etr-core/firebase-bridge.env"
SERVICE_FILE="/etc/systemd/system/etr-firebase-bridge.service"

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez ce script avec sudo."
  exit 1
fi

apt-get update
apt-get install -y python3-venv
install -d -m 0755 "$APP_DIR" /etc/etr-core
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install "requests>=2.31,<3"
install -m 0755 "$REPO_DIR/src/firebase_bridge.py" "$APP_DIR/firebase_bridge.py"

if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'ENV'
FIREBASE_API_KEY=
FIREBASE_AUTH_EMAIL=
FIREBASE_AUTH_PASSWORD=
FIREBASE_DATABASE_URL=
ETR_INSTALLATION_ID=etr-core
ETR_LOCAL_API_URL=http://127.0.0.1:8080/
ETR_BRIDGE_INTERVAL=15
ENV
  chmod 0600 "$ENV_FILE"
fi

cat > "$SERVICE_FILE" <<'UNIT'
[Unit]
Description=Passerelle sortante EtR vers Firebase
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/etr-core/firebase-bridge.env
ExecStart=/opt/etr-core/venv/bin/python /opt/etr-core/firebase_bridge.py
Restart=always
RestartSec=10
User=root
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable etr-firebase-bridge.service

echo "Installation terminée."
echo "1. Complétez $ENV_FILE avec: sudo nano $ENV_FILE"
echo "2. Démarrez avec: sudo systemctl restart etr-firebase-bridge"
echo "3. Contrôlez avec: sudo journalctl -u etr-firebase-bridge -f"
