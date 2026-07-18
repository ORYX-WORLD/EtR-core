#!/usr/bin/env bash

for _ in $(seq 1 30); do
  if DISPLAY=:1 XAUTHORITY=/home/oryx/.Xauthority \
       /usr/bin/xset q >/dev/null 2>&1; then
    DISPLAY=:1 XAUTHORITY=/home/oryx/.Xauthority /usr/bin/xset s off
    DISPLAY=:1 XAUTHORITY=/home/oryx/.Xauthority /usr/bin/xset s noblank
    DISPLAY=:1 XAUTHORITY=/home/oryx/.Xauthority /usr/bin/xset -dpms
    DISPLAY=:1 XAUTHORITY=/home/oryx/.Xauthority /usr/bin/xset s reset
    exit 0
  fi
  sleep 1
done

echo "etr-disable-blanking: écran :1 indisponible après 30s, veille non désactivée" >&2
exit 0
