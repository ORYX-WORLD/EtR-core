#!/usr/bin/env bash
# Garde Chromium dans une coque tactile locale stable. Le portail affiche
# lui-même le tableau EtR seulement lorsque le réseau et le service répondent.
set -Eeuo pipefail

export DISPLAY=:1
export XAUTHORITY=/home/oryx/.Xauthority
export LANG=fr_FR.UTF-8
export LANGUAGE=fr_FR:fr
export LC_ALL=fr_FR.UTF-8

for _ in $(seq 1 60); do
  [ -S /tmp/.X11-unix/X1 ] && break
  sleep 1
done
[ -S /tmp/.X11-unix/X1 ] || {
  echo "Écran X :1 indisponible" >&2
  exit 1
}

for _ in $(seq 1 60); do
  nc -z 127.0.0.1 8090 && break
  sleep 1
done
nc -z 127.0.0.1 8090 || {
  echo "Le portail tactile EtR ne répond pas" >&2
  exit 1
}

ETR_URL="http://127.0.0.1:8090"
install -d -m 700 /home/oryx/.cache/etr-kiosk-chromium

pkill -TERM -u oryx -f '[c]hromium.*etr-kiosk-chromium' 2>/dev/null || true
sleep 2
pkill -KILL -u oryx -f '[c]hromium.*etr-kiosk-chromium' 2>/dev/null || true
rm -f /home/oryx/.cache/etr-kiosk-chromium/SingletonCookie \
      /home/oryx/.cache/etr-kiosk-chromium/SingletonLock \
      /home/oryx/.cache/etr-kiosk-chromium/SingletonSocket

exec /usr/bin/chromium \
  --kiosk \
  --ozone-platform=x11 \
  --noerrdialogs \
  --no-first-run \
  --incognito \
  --lang=fr-FR \
  --disable-translate \
  --disable-features=Translate,TranslateUI \
  --disable-background-networking \
  --disable-component-update \
  --disable-breakpad \
  --disable-gpu \
  --overscroll-history-navigation=0 \
  --user-data-dir=/home/oryx/.cache/etr-kiosk-chromium \
  "$ETR_URL"
