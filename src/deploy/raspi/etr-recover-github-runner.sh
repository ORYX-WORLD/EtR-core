#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_BASE=/home/oryx/actions-runner
PROOF=/tmp/etr-runner-recovery.txt
RESTART=1

if [ "${1:-}" = "--no-restart" ]; then
  RESTART=0
elif [ "$#" -gt 0 ]; then
  echo "usage: $0 [--no-restart]" >&2
  exit 2
fi

find_runner_dir() {
  local candidate
  for candidate in "$RUNNER_BASE/actions-runner" "$RUNNER_BASE"; do
    if [ -x "$candidate/bin/Runner.Listener" ] && [ -x "$candidate/runsvc.sh" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

RUNNER_DIR=$(find_runner_dir || true)

{
  echo "checked_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host=$(hostname)"
  echo "runner_dir=${RUNNER_DIR:-none}"
} > "$PROOF"

if [ -z "$RUNNER_DIR" ]; then
  echo "runner_installation=absent" >> "$PROOF"
  exit 1
fi

mapfile -t units < <(
  systemctl list-unit-files --type=service --no-legend 'actions.runner*.service' 2>/dev/null \
    | awk '{print $1}' \
    | grep -E '^actions\.runner\..*\.service$' \
    || true
)

if [ "${#units[@]}" -gt 0 ]; then
  for unit in "${units[@]}"; do
    echo "service=$unit" >> "$PROOF"
    install -d -m 0755 "/etc/systemd/system/$unit.d"
    cat > "/etc/systemd/system/$unit.d/10-etr-persistence.conf" <<'EOF'
[Service]
Restart=always
RestartSec=10s

[Unit]
StartLimitIntervalSec=300
StartLimitBurst=10
EOF
    systemctl daemon-reload
    systemctl enable "$unit"
    if [ "$RESTART" -eq 1 ]; then
      systemctl restart "$unit"
    fi
    systemctl is-enabled "$unit" >> "$PROOF" 2>&1 || true
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
if [ "$RESTART" -eq 1 ] && [ -x "$RUNNER_DIR/runsvc.sh" ]; then
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
