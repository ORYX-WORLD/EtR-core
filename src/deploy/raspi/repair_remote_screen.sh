#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=${ETR_INSTALL_DIR:-/home/oryx/EtR-core}
ENV_FILE=${ETR_ENV_FILE:-/etc/etr-core/firebase-bridge.env}
STATE_DIR=${ETR_STATE_DIR:-/var/lib/etr-core}
TOKEN_FILE=${STATE_DIR}/firebase-auth.json
UNIT_NAME=etr-remote-screen.service
UNIT_FILE=/etc/systemd/system/${UNIT_NAME}
DROPIN_DIR=/etc/systemd/system/${UNIT_NAME}.d

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
# à son UID dans deviceAccess. Cette liaison est l'autorité déjà utilisée par
# Firebase et Cloud Run ; sur le premier équipement, elle vaut encore etr-core.
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
    PYTHONPATH="$ETR_INSTALL_DIR" ./.venv/bin/python - <<"PY"
from pathlib import Path
import os

from src.firebase_bridge import atomic_json_write, load_json, refresh_tokens
from src.remote_screen_agent import installation_id_from_local_device
from src.remote_screen_identity import resolve_remote_installation_id

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

# Supprimer l'ancien état indépendant ET les anciens drop-ins systemd. Un
# token.conf historique pouvait remplacer l'Environment de l'unité versionnée
# et remettre ETR_TOKEN_FILE sur remote-screen-auth.json.
sudo systemctl stop "$UNIT_NAME" 2>/dev/null || true
sudo rm -f "$STATE_DIR/remote-screen-auth.json" "$STATE_DIR/remote-screen-auth.tmp"
sudo rm -rf "$DROPIN_DIR"

sudo install -m 644 "$INSTALL_DIR/src/deploy/raspi/etr-vnc.service" /etc/systemd/system/etr-vnc.service
sudo install -m 644 "$INSTALL_DIR/src/deploy/raspi/etr-remote-screen.service" "$UNIT_FILE"
sudo systemctl daemon-reload

loaded_unit=$(sudo systemctl cat "$UNIT_NAME")
grep -Fq 'ETR_TOKEN_FILE=/var/lib/etr-core/firebase-auth.json' <<<"$loaded_unit"
if grep -Fq 'ETR_TOKEN_FILE=/var/lib/etr-core/remote-screen-auth.json' <<<"$loaded_unit"; then
  echo "Ancien fichier de jetons encore chargé par systemd" >&2
  exit 4
fi

sudo systemctl enable etr-firebase-bridge.service etr-vnc.service "$UNIT_NAME"
sudo systemctl restart etr-firebase-bridge.service
sudo systemctl restart etr-vnc.service
sudo systemctl restart "$UNIT_NAME"

for service in etr-firebase-bridge.service etr-vnc.service "$UNIT_NAME"; do
  ready=false
  for _ in $(seq 1 45); do
    if sudo systemctl is-active --quiet "$service"; then
      ready=true
      break
    fi
    sleep 2
  done
  [ "$ready" = true ] || {
    sudo systemctl status "$service" --no-pager -l || true
    sudo journalctl -u "$service" -n 120 --no-pager || true
    exit 5
  }
done

echo "ETR_INSTALLATION_ID=${installation_id}"
echo "ETR_TOKEN_FILE=${TOKEN_FILE}"
echo "Réparation de l'écran distant appliquée : liaison deviceAccess, session partagée, drop-ins obsolètes supprimés et services réalignés."
