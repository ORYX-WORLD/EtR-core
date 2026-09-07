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
