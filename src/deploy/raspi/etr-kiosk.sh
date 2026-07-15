#!/usr/bin/env bash
# Démarre Chromium lorsque X et l'une des interfaces EtR répondent.
set -Eeuo pipefail

export DISPLAY=:1
export XAUTHORITY=/home/oryx/.Xauthority

for _ in $(seq 1 60); do
  [ -S /tmp/.X11-unix/X1 ] && break
  sleep 1
done
[ -S /tmp/.X11-unix/X1 ] || {
  echo "Écran X :1 indisponible" >&2
  exit 1
}

# Le portail réseau est le filet de sécurité. En présence d'une connexion,
# l'écran conserve le tableau de bord pression/compresseur existant.
for _ in $(seq 1 60); do
  nc -z 127.0.0.1 8000 && break
  nc -z 127.0.0.1 8090 && break
  sleep 1
done

HOTSPOT_ACTIVE=false
if nmcli -t -f NAME,TYPE connection show --active 2>/dev/null | grep -q '^etr-setup:'; then
  HOTSPOT_ACTIVE=true
fi

if [ ! -f /var/lib/etr-core/commissioned ] && nc -z 127.0.0.1 8090; then
  # Premier démarrage : l'assistant reste affiché, même avec Ethernet, jusqu'à
  # la validation tactile de la mise en service.
  ETR_URL="http://127.0.0.1:8090"
elif [ "$HOTSPOT_ACTIVE" = false ] && nm-online -q --timeout=8 && nc -z 127.0.0.1 8000; then
  ETR_URL="http://127.0.0.1:8000"
elif nc -z 127.0.0.1 8090; then
  ETR_URL="http://127.0.0.1:8090"
else
  echo "Ni le tableau de bord ni le portail réseau EtR ne répondent" >&2
  exit 1
fi

install -d -m 700 /home/oryx/.cache/etr-kiosk-chromium

exec /usr/bin/chromium \
  --kiosk \
  --ozone-platform=x11 \
  --noerrdialogs \
  --no-first-run \
  --incognito \
  --disable-translate \
  --disable-background-networking \
  --disable-component-update \
  --disable-breakpad \
  --disable-gpu \
  --overscroll-history-navigation=0 \
  --user-data-dir=/home/oryx/.cache/etr-kiosk-chromium \
  "$ETR_URL"
