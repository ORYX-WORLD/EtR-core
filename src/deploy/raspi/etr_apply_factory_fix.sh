#!/usr/bin/env bash
set -euo pipefail

repo=/home/oryx/EtR-core
state=/run/etr-maintenance.json
python="$repo/.venv/bin/python"
ready=/tmp/etr-maintenance-ui-ready

progress() {
  local pct="$1" msg="$2" done="${3:-false}"
  printf '{"title":"Maintenance EtR","message":"%s","progress":%s,"done":%s}\n' "$msg" "$pct" "$done" | tee "$state" >/dev/null
  chmod 644 "$state" 2>/dev/null || true
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

progress 18 "Installation du suivi visuel..."
install -m 644 src/deploy/raspi/etr-maintenance-overlay.service /etc/systemd/system/etr-maintenance-overlay.service
systemctl daemon-reload
rm -f "$ready"
systemctl stop etr-maintenance-overlay.service 2>/dev/null || true
systemctl reset-failed etr-maintenance-overlay.service 2>/dev/null || true
systemctl start etr-maintenance-overlay.service

# Validation fonctionnelle : le processus doit être actif ET Tk doit avoir reçu
# l'événement Map de la fenêtre sur le vrai DISPLAY du bureau Raspberry.
for _ in $(seq 1 20); do
  if systemctl is-active --quiet etr-maintenance-overlay.service && [ -s "$ready" ]; then
    break
  fi
  sleep 0.5
done
systemctl is-active --quiet etr-maintenance-overlay.service
test -s "$ready"
pgrep -u oryx -af 'etr_maintenance_overlay.py' >/dev/null

progress 30 "Suivi visuel actif sur le bureau Raspberry"
sleep 2

progress 40 "Installation du raccourci SD V1.1..."
install -m 755 src/deploy/raspi/etr-sd-factory-launch.sh /usr/local/bin/etr-sd-factory-launch.sh
rm -f /home/oryx/Desktop/etr-sd-factory.desktop
install -m 755 src/deploy/raspi/etr-sd-factory.desktop /home/oryx/Desktop/etr-sd-factory.desktop
chown oryx:oryx /home/oryx/Desktop/etr-sd-factory.desktop
command -v gio >/dev/null 2>&1 && sudo -u oryx gio set /home/oryx/Desktop/etr-sd-factory.desktop metadata::trusted true 2>/dev/null || true
grep -q '^Name=SD V1.1$' /home/oryx/Desktop/etr-sd-factory.desktop
grep -q '^Exec=sudo -n /usr/local/bin/etr-sd-factory-launch.sh$' /home/oryx/Desktop/etr-sd-factory.desktop

progress 55 "Installation de la Fabrique resiliente..."
install -m 644 src/deploy/raspi/etr-sd-factory.service /etc/systemd/system/etr-sd-factory.service
install -m 755 src/deploy/raspi/etr-network-resilience.sh /usr/local/bin/etr-network-resilience.sh
install -m 644 src/deploy/raspi/etr-network-resilience.service /etc/systemd/system/etr-network-resilience.service
install -m 644 src/deploy/raspi/etr-network-resilience.timer /etc/systemd/system/etr-network-resilience.timer
systemctl daemon-reload
systemctl enable --now etr-network-resilience.timer

progress 70 "Verification DNS et Internet depuis Python..."
"$python" - <<'PY'
import socket
import requests
host = 'etr-remote-gateway-7n72m5gopq-ew.a.run.app'
socket.getaddrinfo(host, 443)
r = requests.get(f'https://{host}/api/health', timeout=12)
r.raise_for_status()
print('PYTHON_NETWORK_OK')
PY

progress 84 "Relance de la Fabrique depuis le vrai service..."
systemctl stop etr-sd-factory.service 2>/dev/null || true
systemctl reset-failed etr-sd-factory.service 2>/dev/null || true
systemctl start etr-sd-factory.service
sleep 4
systemctl is-active --quiet etr-sd-factory.service

grep -q 'etr_sd_factory_resilient.py' /etc/systemd/system/etr-sd-factory.service
grep -q 'etr-sd-factory.service' /usr/local/bin/etr-sd-factory-launch.sh
systemctl show -p ExecStart --value etr-sd-factory.service | grep -q 'etr_sd_factory_resilient.py'
pgrep -af 'etr_sd_factory_resilient.py' >/dev/null

progress 100 "Mise a jour terminee - SD V1.1 prete" true
sleep 5
echo ETR_FACTORY_FIX_OK
