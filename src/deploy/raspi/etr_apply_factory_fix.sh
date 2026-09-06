#!/usr/bin/env bash
set -euo pipefail

repo=/home/oryx/EtR-core
state=/run/etr-maintenance.json
python="$repo/.venv/bin/python"

progress() {
  local pct="$1" msg="$2" done="${3:-false}"
  printf '{"title":"Maintenance EtR","message":"%s","progress":%s,"done":%s}\n' "$msg" "$pct" "$done" | tee "$state" >/dev/null
}

fail() {
  progress 100 "Echec de la mise a jour - diagnostic requis" true
  echo ETR_FACTORY_FIX_FAIL
}
trap fail ERR

cd "$repo"
progress 10 "Mise a jour du systeme EtR..."
git fetch origin main
git reset --hard origin/main

progress 20 "Affichage du suivi sur le Raspberry..."
DISPLAY=:1 XAUTHORITY=/home/oryx/.Xauthority "$python" "$repo/src/deploy/raspi/etr_maintenance_overlay.py" >/tmp/etr-maintenance-ui.log 2>&1 &
sleep 2

progress 40 "Installation de la Fabrique resiliente..."
install -m 644 src/deploy/raspi/etr-sd-factory.service /etc/systemd/system/etr-sd-factory.service
install -m 755 src/deploy/raspi/etr-network-resilience.sh /usr/local/bin/etr-network-resilience.sh
install -m 644 src/deploy/raspi/etr-network-resilience.service /etc/systemd/system/etr-network-resilience.service
install -m 644 src/deploy/raspi/etr-network-resilience.timer /etc/systemd/system/etr-network-resilience.timer
systemctl daemon-reload
systemctl enable --now etr-network-resilience.timer

progress 65 "Verification DNS et Internet depuis Python..."
"$python" - <<'PY'
import socket
import requests
host = 'etr-remote-gateway-7n72m5gopq-ew.a.run.app'
socket.getaddrinfo(host, 443)
r = requests.get(f'https://{host}/api/health', timeout=12)
r.raise_for_status()
print('PYTHON_NETWORK_OK')
PY

progress 82 "Relance de la Fabrique..."
systemctl restart etr-sd-factory.service
sleep 4
systemctl is-active --quiet etr-sd-factory.service
grep -q 'etr_sd_factory_resilient.py' /etc/systemd/system/etr-sd-factory.service

progress 100 "Mise a jour terminee - Fabrique prete" true
echo ETR_FACTORY_FIX_OK
