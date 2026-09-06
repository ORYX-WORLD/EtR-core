#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${ETR_NETWORK_TARGET_HOST:-etr-remote-gateway-7n72m5gopq-ew.a.run.app}"

log() { logger -t etr-network-resilience -- "$*" 2>/dev/null || true; }

# Rien a faire sans NetworkManager ni interface connectee.
command -v nmcli >/dev/null 2>&1 || exit 0

active_line=$(nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status | awk -F: '$3=="connected" && ($2=="wifi" || $2=="ethernet") {print; exit}')
[ -n "${active_line:-}" ] || exit 0

iface=$(printf '%s' "$active_line" | cut -d: -f1)
conn=$(printf '%s' "$active_line" | cut -d: -f4-)
[ -n "$iface" ] && [ -n "$conn" ] || exit 0

resolve_ok=false
if getent ahosts "$TARGET_HOST" >/dev/null 2>&1; then
  if python3 - "$TARGET_HOST" <<'PY' >/dev/null 2>&1
import socket, sys
socket.getaddrinfo(sys.argv[1], 443, type=socket.SOCK_STREAM)
PY
  then
    resolve_ok=true
  fi
fi

if [ "$resolve_ok" = true ]; then
  log "DNS OK sur $iface ($conn)"
  exit 0
fi

log "DNS en echec sur $iface ($conn), activation des DNS de secours EtR"

# On ne depend jamais du nom du Wi-Fi : la connexion active est detectee dynamiquement.
# Les DNS recus par DHCP restent la configuration normale. Les DNS publics ne sont poses
# qu'en secours lorsqu'une resolution reelle du gateway EtR echoue.
nmcli connection modify "$conn" ipv4.ignore-auto-dns yes ipv4.dns "1.1.1.1 8.8.8.8" || true
if nmcli -g ipv6.method connection show "$conn" 2>/dev/null | grep -vq '^disabled$'; then
  nmcli connection modify "$conn" ipv6.ignore-auto-dns yes ipv6.dns "2606:4700:4700::1111 2001:4860:4860::8888" || true
fi

nmcli device reapply "$iface" >/dev/null 2>&1 || true
command -v resolvectl >/dev/null 2>&1 && resolvectl flush-caches >/dev/null 2>&1 || true
sleep 2

if getent ahosts "$TARGET_HOST" >/dev/null 2>&1 && python3 - "$TARGET_HOST" <<'PY' >/dev/null 2>&1
import socket, sys
socket.getaddrinfo(sys.argv[1], 443, type=socket.SOCK_STREAM)
PY
then
  log "DNS restaure sur $iface ($conn)"
  systemctl try-restart etr-sd-factory.service etr-sd-factory-worker.service etr-remote-screen.service 2>/dev/null || true
  exit 0
fi

log "DNS toujours indisponible sur $iface ($conn) apres secours"
exit 1
