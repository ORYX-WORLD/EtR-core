#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=${ETR_INSTALL_DIR:-/home/oryx/EtR-core}
ENV_FILE=${ETR_ENV_FILE:-/etc/etr-core/firebase-bridge.env}
STATE_DIR=${ETR_STATE_DIR:-/var/lib/etr-core}

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
installation_id="etr-${serial: -12}"
installation_id=$(printf '%s' "$installation_id" | tr '[:upper:]' '[:lower:]')

sudo install -d -m 750 -o root -g oryx "$(dirname "$ENV_FILE")"
sudo touch "$ENV_FILE"
sudo sed -i '/^ETR_INSTALLATION_ID=/d' "$ENV_FILE"
printf '%s\n' "ETR_INSTALLATION_ID=${installation_id}" | sudo tee -a "$ENV_FILE" >/dev/null
sudo chown root:oryx "$ENV_FILE"
sudo chmod 640 "$ENV_FILE"
sudo grep -q '^ETR_REMOTE_GATEWAY_WSS=wss://.*\.run\.app/device$' "$ENV_FILE"

sudo install -d -m 700 -o oryx -g oryx "$STATE_DIR"
sudo rm -f "$STATE_DIR/remote-screen-auth.json" "$STATE_DIR/remote-screen-auth.tmp"

# Le relais partage exclusivement la session appareil déjà créée par le bridge.
# Une session absente doit faire échouer la réparation plutôt que déclencher un
# deuxième enrôlement et un second code d'activation.
sudo -u oryx -H python3 - "$STATE_DIR/firebase-auth.json" <<'PY'
import json, sys
from pathlib import Path
path=Path(sys.argv[1])
try:
    data=json.loads(path.read_text(encoding='utf-8'))
except Exception as error:
    raise SystemExit(f'Session Firebase appareil illisible: {type(error).__name__}')
for key in ('idToken','refreshToken'):
    value=data.get(key)
    if not isinstance(value,str) or len(value) <= 20:
        raise SystemExit(f'Session Firebase appareil incomplète: {key}')
PY
sudo chown oryx:oryx "$STATE_DIR/firebase-auth.json"
sudo chmod 600 "$STATE_DIR/firebase-auth.json"

# Réinstaller explicitement les unités versionnées : le précédent diagnostic
# avait pu remettre une ancienne unité utilisant un fichier de jetons séparé.
sudo install -m 644 "$INSTALL_DIR/src/deploy/raspi/etr-vnc.service" /etc/systemd/system/etr-vnc.service
sudo install -m 644 "$INSTALL_DIR/src/deploy/raspi/etr-remote-screen.service" /etc/systemd/system/etr-remote-screen.service
sudo systemctl daemon-reload
sudo systemctl enable etr-firebase-bridge.service etr-vnc.service etr-remote-screen.service
sudo systemctl restart etr-firebase-bridge.service
sudo systemctl restart etr-vnc.service
sudo systemctl restart etr-remote-screen.service

echo "ETR_INSTALLATION_ID=${installation_id}"
echo "Réparation de l'écran distant appliquée ; identité, session partagée et services réalignés."
