#!/usr/bin/env bash
set -Eeuo pipefail

unit=actions.runner.ORYX-WORLD-EtR-core.etr-core.service
state=/run/etr-runner-watchdog.failures

systemctl is-enabled --quiet "$unit" || exit 0
systemctl is-active --quiet "$unit" || {
  systemctl restart "$unit"
  rm -f "$state"
  exit 0
}

# Ne jamais interrompre un workflow en cours.
pgrep -f '/bin/Runner.Worker ' >/dev/null && {
  rm -f "$state"
  exit 0
}

listener_pid=$(pgrep -o -f '/bin/Runner.Listener run ' || true)
queued_physical_job=$(python3 - <<'PY' || true
import json
import urllib.request
from datetime import datetime, timezone

physical = {
    "Déployer l'application EtR complète",
    "EtR - preuve runner apres reboot",
    "EtR - rapport post reboot detaille",
    "EtR - diagnostic ecran SPI apres boot",
    "EtR - reparer affichage physique actif",
    "EtR - recuperer services apres deploiement interrompu",
}
request = urllib.request.Request(
    "https://api.github.com/repos/ORYX-WORLD/EtR-core/actions/runs"
    "?status=queued&per_page=30",
    headers={"Accept": "application/vnd.github+json", "User-Agent": "etr-runner-watchdog"},
)
with urllib.request.urlopen(request, timeout=10) as response:
    runs = json.load(response).get("workflow_runs", [])
now = datetime.now(timezone.utc)
old = any(
    run.get("name") in physical
    and (now - datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))).total_seconds() >= 300
    for run in runs
)
print("yes" if old else "no")
PY
)

if [ "$queued_physical_job" = yes ]; then
  logger -t etr-runner-watchdog \
    "job physique en attente depuis plus de cinq minutes; redemarrage de $unit"
  systemctl restart "$unit"
  rm -f "$state"
  exit 0
fi

if [ -n "$listener_pid" ] && ss -Htnp state established 2>/dev/null \
  | grep -q "pid=$listener_pid,"; then
  rm -f "$state"
  exit 0
fi

failures=0
[ -r "$state" ] && read -r failures < "$state" || true
failures=$((failures + 1))
printf '%s\n' "$failures" > "$state"

# Trois constats espacés par le timer évitent un redémarrage sur une simple
# reconnexion TLS. Le runner bloqué reste récupérable en moins de six minutes.
if [ "$failures" -ge 3 ]; then
  logger -t etr-runner-watchdog \
    "runner sans connexion et sans job actif; redemarrage de $unit"
  systemctl restart "$unit"
  rm -f "$state"
fi
