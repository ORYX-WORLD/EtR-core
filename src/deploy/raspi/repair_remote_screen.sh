#!/usr/bin/env bash
set -euo pipefail
# Relance opérationnelle après rétablissement explicite du trafic Cloud Run.

INSTALL_DIR=${ETR_INSTALL_DIR:-/home/oryx/EtR-core}
ENV_FILE=${ETR_ENV_FILE:-/etc/etr-core/firebase-bridge.env}
STATE_DIR=${ETR_STATE_DIR:-/var/lib/etr-core}
TOKEN_FILE=${STATE_DIR}/firebase-auth.json

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
            if line.lower().startswith('serial'):
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

# Certaines sessions appareil historiques possèdent etrDevice=true sans le
# claim installationId. Le relais sait déjà utiliser dans ce cas l'identité
# canonique dérivée du numéro de série ; la réparation applique la même règle.
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
if payload.get('etrDevice') is not True:
    raise SystemExit('Claim etrDevice absent')
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

# Supprimer l'ancien état indépendant : le relais partage exclusivement la
# session Firebase déjà enrôlée du bridge principal.
sudo rm -f "$STATE_DIR/remote-screen-auth.json" "$STATE_DIR/remote-screen-auth.tmp"

sudo install -m 644 "$INSTALL_DIR/src/deploy/raspi/etr-vnc.service" /etc/systemd/system/etr-vnc.service
sudo install -m 644 "$INSTALL_DIR/src/deploy/raspi/etr-remote-screen.service" /etc/systemd/system/etr-remote-screen.service
sudo systemctl daemon-reload
sudo systemctl enable etr-firebase-bridge.service etr-vnc.service etr-remote-screen.service
sudo systemctl restart etr-firebase-bridge.service
sudo systemctl restart etr-vnc.service
sudo systemctl restart etr-remote-screen.service

echo "ETR_INSTALLATION_ID=${installation_id}"
echo "Réparation de l'écran distant appliquée : identité canonique, session partagée et services réalignés."
