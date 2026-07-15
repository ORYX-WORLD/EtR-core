#!/usr/bin/env bash
# Démarre Chromium uniquement lorsque l'écran X et le tableau de bord EtR répondent.
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

for _ in $(seq 1 60); do
  nc -z 127.0.0.1 8000 && break
  sleep 1
done
nc -z 127.0.0.1 8000 || {
  echo "Tableau de bord EtR indisponible sur le port 8000" >&2
  exit 1
}

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
  http://127.0.0.1:8000
