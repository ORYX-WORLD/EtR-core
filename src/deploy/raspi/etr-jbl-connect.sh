#!/usr/bin/env bash
set -euo pipefail

JBL_MAC="${ETR_JBL_MAC:-40:C1:F6:70:C0:1A}"
JBL_NAME="${ETR_JBL_NAME:-JBL Go 3}"
STATE_FILE="${ETR_AUDIO_OUTPUT_STATE:-/var/lib/etr-core/audio-output.json}"
INTERVAL="${ETR_JBL_RETRY_SECONDS:-30}"
ONCE=false
[ "${1:-}" = "--once" ] && ONCE=true

mkdir -p "$(dirname "$STATE_FILE")"

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().rstrip("\n"), ensure_ascii=False))'
}

write_state() {
  local connected=$1
  local sink=${2:-}
  local message=${3:-}
  local tmp="${STATE_FILE}.tmp"
  cat > "$tmp" <<EOF
{
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "device_name": "$JBL_NAME",
  "device_mac": "$JBL_MAC",
  "connected": $connected,
  "sink": "$sink",
  "message": $(printf '%s' "$message" | json_escape)
}
EOF
  chown oryx:oryx "$tmp" 2>/dev/null || true
  chmod 600 "$tmp"
  mv "$tmp" "$STATE_FILE"
}

as_oryx() {
  runuser -u oryx -- env \
    XDG_RUNTIME_DIR=/run/user/1000 \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
    "$@"
}

find_sink() {
  as_oryx /usr/bin/pactl list short sinks 2>/dev/null \
    | awk -v mac="${JBL_MAC//:/_}" '$2 ~ /^bluez_output\./ && ($2 ~ mac || first == "") { if (first == "") first=$2; if ($2 ~ mac) {print $2; exit} } END {if (NR && !printed && first != "") print first}' \
    | head -n 1
}

connect_once() {
  /usr/bin/bluetoothctl power on >/dev/null 2>&1 || true
  /usr/bin/bluetoothctl agent on >/dev/null 2>&1 || true
  /usr/bin/bluetoothctl default-agent >/dev/null 2>&1 || true
  /usr/bin/bluetoothctl trust "$JBL_MAC" >/dev/null 2>&1 || true

  if ! /usr/bin/bluetoothctl info "$JBL_MAC" 2>/dev/null | grep -q 'Paired: yes'; then
    /usr/bin/bluetoothctl --timeout 20 pair "$JBL_MAC" >/tmp/etr-jbl-pair.log 2>&1 || true
  fi
  /usr/bin/bluetoothctl --timeout 20 connect "$JBL_MAC" >/tmp/etr-jbl-connect.log 2>&1 || true

  local connected=false
  local sink=""
  for _ in $(seq 1 30); do
    if /usr/bin/bluetoothctl info "$JBL_MAC" 2>/dev/null | grep -q 'Connected: yes'; then
      connected=true
    fi
    sink=$(find_sink || true)
    if [ "$connected" = true ] && [ -n "$sink" ]; then
      as_oryx /usr/bin/pactl set-default-sink "$sink" >/dev/null
      as_oryx /usr/bin/pactl set-sink-volume "$sink" 35% >/dev/null || true
      write_state true "$sink" "Enceinte Bluetooth connectée et définie comme sortie audio EtR."
      return 0
    fi
    sleep 1
  done

  local detail
  detail=$(tail -n 4 /tmp/etr-jbl-connect.log 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g' || true)
  write_state false "$sink" "Connexion JBL incomplète. ${detail:-Vérifier que l'enceinte est allumée et en mode association.}"
  return 1
}

if [ "$ONCE" = true ]; then
  connect_once
  exit $?
fi

while true; do
  connect_once || true
  sleep "$INTERVAL"
done
