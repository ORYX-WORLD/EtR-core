#!/usr/bin/env bash
set -euo pipefail
# Réparation idempotente du relais écran après mise à jour Cloud Run.

INSTALL_DIR=${ETR_INSTALL_DIR:-/home/oryx/EtR-core}
ENV_FILE=${ETR_ENV_FILE:-/etc/etr-core/firebase-bridge.env}
STATE_DIR=${ETR_STATE_DIR:-/var/lib/etr-core}
TOKEN_FILE=${STATE_DIR}/firebase-auth.json
UNIT_NAME=etr-remote-screen.service
UNIT_FILE=/etc/systemd/system/${UNIT_NAME}
DROPIN_DIR=/etc/systemd/system/${UNIT_NAME}.d

serial=$(python3 - <<'PY'
from pathlib import Path
import re
value = ""
for path in [Path('/sys/firmware/devicetree/base/serial-number'), Path('/proc/device-tree/serial-number')]:
    try:
        value = path.read_bytes().replace(b'\x00', b'').decode('ascii').strip()
        if value:
            break
    except (OSError, UnicodeDecodeError):
        pass
if not value:
    try:
        for line in Path('/proc/cpuinfo').read_text(encoding='utf-8').splitlines():
            if line.lower().startswith('serial') and ':' in line:
                value = line.split(':', 1)[1].strip()
                break
    except OSError:
        pass
serial = re.sub(r'[^A-Za-z0-9]', '', value).upper()
if len(serial) < 8:
    raise SystemExit('Numéro de série Raspberry introuvable')
print(serial[-64:])
PY
)
derived_installation_id="etr-${serial: -12}"
derived_installation_id=$(printf '%s' "$derived_installation_id" | tr '[:upper:]' '[:lower:]')

sudo install -d -m 700 -o oryx -g oryx "$STATE_DIR"
sudo test -s "$TOKEN_FILE" || {
  echo "Session Firebase appareil absente : $TOKEN_FILE" >&2
  exit 2
}
sudo chown oryx:oryx "$TOKEN_FILE"
sudo chmod 600 "$TOKEN_FILE"

# Les premières sessions directes peuvent ne contenir ni installationId ni
# etrDevice. Le relais ne prend aucune décision d'autorisation à partir de cette
# lecture locale : Cloud Run valide le JWT puis exige deviceAccess/<uid> avant
# d'accepter la WebSocket. Ici, on utilise seulement installationId lorsqu'il
# existe et qu'il est bien formé ; sinon l'identité matérielle canonique.
signed_installation_id=$(sudo -u oryx -H python3 - "$TOKEN_FILE" <<'PY'
import base64, json, re, sys
from pathlib import Path
path=Path(sys.argv[1])
data=json.loads(path.read_text(encoding='utf-8'))
for key in ('idToken','refreshToken'):
    value=data.get(key)
    if not isinstance(value,str) or len(value) <= 20:
        raise SystemExit(f'Session Firebase appareil incomplète: {key}')
parts=data['idToken'].split('.')
if len(parts) != 3:
    raise SystemExit('ID token Firebase invalide')
payload=json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)).decode('utf-8'))
installation_id=str(payload.get('installationId') or '').strip()
if installation_id and not re.fullmatch(r'[A-Za-z0-9._-]{2,80}', installation_id):
    raise SystemExit('Claim installationId invalide')
print(installation_id)
PY
)

if [ -n "$signed_installation_id" ] && [ "$signed_installation_id" != "$derived_installation_id" ]; then
  echo "Identité Firebase ($signed_installation_id) différente de l'identité matérielle ($derived_installation_id)" >&2
  exit 3
fi
installation_id=${signed_installation_id:-$derived_installation_id}

sudo install -d -m 750 -o root -g oryx "$(dirname "$ENV_FILE")"
sudo touch "$ENV_FILE"
sudo sed -i '/^ETR_INSTALLATION_ID=/d' "$ENV_FILE"
printf '%s\n' "ETR_INSTALLATION_ID=${installation_id}" | sudo tee -a "$ENV_FILE" >/dev/null
sudo chown root:oryx "$ENV_FILE"
sudo chmod 640 "$ENV_FILE"
sudo grep -q '^ETR_REMOTE_GATEWAY_WSS=wss://.*\.run\.app/device$' "$ENV_FILE"

# Supprimer l'ancien état et surtout les anciens drop-ins systemd. Un ancien
# token.conf pouvait remplacer l'Environment du fichier d'unité versionné et
# remettre ETR_TOKEN_FILE sur remote-screen-auth.json malgré l'installation du
# bon service.
sudo systemctl stop "$UNIT_NAME" 2>/dev/null || true
sudo rm -f "$STATE_DIR/remote-screen-auth.json" "$STATE_DIR/remote-screen-auth.tmp"
sudo rm -rf "$DROPIN_DIR"
sudo install -m 644 "$INSTALL_DIR/src/deploy/raspi/etr-vnc.service" /etc/systemd/system/etr-vnc.service
sudo install -m 644 "$INSTALL_DIR/src/deploy/raspi/etr-remote-screen.service" "$UNIT_FILE"
sudo systemctl daemon-reload

# Prouver la configuration réellement chargée, pas seulement le fichier source.
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
echo "Réparation de l'écran distant appliquée : identité canonique, session partagée, drop-ins obsolètes supprimés et services réalignés."
