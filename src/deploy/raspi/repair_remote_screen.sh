#!/usr/bin/env bash
set -euo pipefail

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

sudo install -d -m 700 -o oryx -g oryx "$STATE_DIR"
for token_file in "$STATE_DIR/firebase-auth.json" "$STATE_DIR/remote-screen-auth.json"; do
  if sudo test -f "$token_file"; then
    sudo chown oryx:oryx "$token_file"
    sudo chmod 600 "$token_file"
  fi
done

# Le jeton de l'agent doit représenter le même compte technique que le bridge.
# Si l'ancien état distinct est absent ou illisible, repartir de la session
# appareil déjà enrôlée plutôt que générer un nouveau code d'activation.
if ! sudo -u oryx -H python3 - "$STATE_DIR/remote-screen-auth.json" <<'PY'
import json, sys
from pathlib import Path
path=Path(sys.argv[1])
try:
    data=json.loads(path.read_text(encoding='utf-8'))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if all(isinstance(data.get(key), str) and len(data[key]) > 20 for key in ('idToken','refreshToken')) else 1)
PY
then
  sudo -u oryx -H cp "$STATE_DIR/firebase-auth.json" "$STATE_DIR/remote-screen-auth.json"
  sudo chmod 600 "$STATE_DIR/remote-screen-auth.json"
fi

sudo systemctl daemon-reload
sudo systemctl restart etr-firebase-bridge.service
sudo systemctl restart etr-vnc.service
sudo systemctl restart etr-remote-screen.service

echo "ETR_INSTALLATION_ID=${installation_id}"
echo "Réparation de l'écran distant appliquée ; services redémarrés."
