#!/usr/bin/env bash
set -Eeuo pipefail

FB=/dev/fb1
SYS=/sys/class/graphics/fb1
DISPLAY_ID=:1
XAUTH=/home/oryx/.Xauthority

[ -e "$FB" ] || { echo "Framebuffer absent: $FB" >&2; exit 1; }
[ -r "$SYS/virtual_size" ] || { echo "virtual_size absent pour fb1" >&2; exit 1; }
[ -r "$SYS/bits_per_pixel" ] || { echo "bits_per_pixel absent pour fb1" >&2; exit 1; }

IFS=, read -r WIDTH HEIGHT < "$SYS/virtual_size"
BPP=$(cat "$SYS/bits_per_pixel")

case "$WIDTH:$HEIGHT:$BPP" in
  *[!0-9,:]*) echo "Geometrie framebuffer invalide: ${WIDTH}x${HEIGHT}x${BPP}" >&2; exit 1 ;;
esac

RAW="+map:${FB}@${WIDTH}x${HEIGHT}x${BPP}"

# Si le noyau expose le stride, on le fournit a x11vnc pour respecter le pas
# reel du framebuffer SPI. Sans stride, x11vnc utilise WxBPP/8.
if [ -r "$SYS/stride" ]; then
  STRIDE=$(cat "$SYS/stride")
  if [[ "$STRIDE" =~ ^[0-9]+$ ]] && [ "$STRIDE" -gt 0 ]; then
    RAW="${RAW}-${STRIDE}"
  fi
fi

echo "EtR VNC rawfb: ${RAW} display=${DISPLAY_ID}"

exec /usr/bin/x11vnc \
  -display "$DISPLAY_ID" \
  -auth "$XAUTH" \
  -rawfb "$RAW" \
  -noviewonly \
  -localhost \
  -forever \
  -shared \
  -nopw \
  -rfbport 5901 \
  -noxdamage
