#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_DIR=/home/oryx/actions-runner
PROOF=/tmp/etr-runner-recovery.txt

{
  echo "checked_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host=$(hostname)"
  echo "runner_dir=$RUNNER_DIR"
} > "$PROOF"

mapfile -t units < <(
  systemctl list-unit-files --type=service --no-legend 'actions.runner*.service' 2>/dev/null \
    | awk '{print $1}' \
    | grep -E '^actions\.runner\..*\.service$' \
    || true
)

if [ "${#units[@]}" -gt 0 ]; then
  for unit in "${units[@]}"; do
    echo "service=$unit" >> "$PROOF"
    systemctl restart "$unit"
    systemctl is-active "$unit" >> "$PROOF" 2>&1 || true
  done
else
  echo "service=none" >> "$PROOF"
fi

sleep 3

if pgrep -af 'Runner.Listener|runsvc.sh' >> "$PROOF" 2>&1; then
  echo "runner_process=active" >> "$PROOF"
  exit 0
fi

# Fallback conservateur uniquement si l'installation officielle existe mais
# qu'aucune unite systemd n'est disponible. On ne reenregistre jamais le runner
# et on ne modifie aucun jeton GitHub.
if [ -x "$RUNNER_DIR/runsvc.sh" ]; then
  echo "fallback=runsvc.sh" >> "$PROOF"
  cd "$RUNNER_DIR"
  nohup ./runsvc.sh >> /tmp/etr-actions-runner.log 2>&1 </dev/null &
  sleep 5
fi

if pgrep -af 'Runner.Listener|runsvc.sh' >> "$PROOF" 2>&1; then
  echo "runner_process=active_after_fallback" >> "$PROOF"
  exit 0
fi

echo "runner_process=absent" >> "$PROOF"
exit 1
