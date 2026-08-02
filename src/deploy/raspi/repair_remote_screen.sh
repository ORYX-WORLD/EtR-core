#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=${ETR_INSTALL_DIR:-/home/oryx/EtR-core}
ENV_FILE=${ETR_ENV_FILE:-/etc/etr-core/firebase-bridge.env}
STATE_DIR=${ETR_STATE_DIR:-/var/lib/etr-core}
TOKEN_FILE=${STATE_DIR}/firebase-auth.json

sudo install -d -m 700 -o oryx -g oryx "$STATE_DIR"
sudo test -s "$TOKEN_FILE" || {
  echo "Session Firebase appareil absente : $TOKEN_FILE" >&2
  exit 2
}
sudo chown oryx:oryx "$TOKEN_FILE"
sudo chmod 600 "$TOKEN_FILE"
sudo test -r "$ENV_FILE" || {
  echo "Configuration Firebase absente : $ENV_FILE" >&2
  exit 2
}

# Rafraîchir la session principale et résoudre l'installation réellement liée
# à son UID dans deviceAccess. Le répertoire src est placé explicitement dans
# PYTHONPATH, car remote_screen_agent importe firebase_bridge comme module
# top-level lorsqu'il est exécuté par systemd.
installation_id=$(sudo -u oryx -H env \
  ETR_ENV_FILE="$ENV_FILE" \
  ETR_INSTALL_DIR="$INSTALL_DIR" \
  ETR_TOKEN_FILE="$TOKEN_FILE" \
  bash -c '
    set -euo pipefail
    set -a
    source "$ETR_ENV_FILE"
    set +a
    cd "$ETR_INSTALL_DIR"
    PYTHONPATH="$ETR_INSTALL_DIR/src:$ETR_INSTALL_DIR" ./.venv/bin/python - <<"PY"
from pathlib import Path
import os

from firebase_bridge import atomic_json_write, load_json, refresh_tokens
from remote_screen_agent import installation_id_from_local_device
from remote_screen_identity import resolve_remote_installation_id

token_file = Path(os.environ["ETR_TOKEN_FILE"])
cached = load_json(token_file)
refresh_token = str(cached.get("refreshToken") or "").strip()
if not refresh_token:
    raise SystemExit("Session Firebase appareil sans refresh token")
refreshed = refresh_tokens(refresh_token)
id_token = str(refreshed.get("idToken") or "").strip()
next_refresh = str(refreshed.get("refreshToken") or refresh_token).strip()
if not id_token or not next_refresh:
    raise SystemExit("Rafraîchissement Firebase incomplet")
installation_id = resolve_remote_installation_id(
    id_token,
    database_url=os.environ.get("FIREBASE_DATABASE_URL", ""),
    local_fallback=installation_id_from_local_device(),
)
atomic_json_write(token_file, {"idToken": id_token, "refreshToken": next_refresh})
print(installation_id)
PY
  ')

[[ "$installation_id" =~ ^[A-Za-z0-9._-]{2,80}$ ]] || {
  echo "Identité d'installation invalide : $installation_id" >&2
  exit 3
}

sudo install -d -m 750 -o root -g oryx "$(dirname "$ENV_FILE")"
sudo sed -i '/^ETR_INSTALLATION_ID=/d' "$ENV_FILE"
printf '%s\n' "ETR_INSTALLATION_ID=${installation_id}" | sudo tee -a "$ENV_FILE" >/dev/null
sudo chown root:oryx "$ENV_FILE"
sudo chmod 640 "$ENV_FILE"
sudo grep -q '^ETR_REMOTE_GATEWAY_WSS=wss://.*\.run\.app/device$' "$ENV_FILE"

# Supprimer l'ancien état indépendant : le relais partage exclusivement la
# session Firebase déjà enrôlée du bridge principal.
sudo rm -f "$STATE_DIR/remote-screen-auth.json" "$STATE_DIR/remote-screen-auth.tmp"

sudo install -m 644 "$INSTALL_DIR/src/deploy/raspi/etr-vnc.service" /etc/systemd/system/etr-vnc.service
sudo install -m 644 "$INSTALL_DIR/src/deploy/raspi/etr-remote-screen.service" /etc/systemd/system/etr-remote-screen.service
sudo systemctl daemon-reload
sudo systemctl enable etr-vnc.service etr-remote-screen.service
sudo systemctl restart etr-vnc.service
sudo systemctl restart etr-remote-screen.service

echo "ETR_INSTALLATION_ID=${installation_id}"
echo "Réparation de l'écran distant appliquée : liaison deviceAccess, session partagée et services écran réalignés."
