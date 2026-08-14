#!/usr/bin/env bash
set -Eeuo pipefail

DISPLAY_ID=":2"
GEOMETRY="${ETR_REMOTE_DESKTOP_GEOMETRY:-1280x720x24}"
X_SOCKET="/tmp/.X11-unix/X2"
XVFB_PID=""
LXDE_PID=""

cleanup() {
  set +e
  [ -n "$LXDE_PID" ] && kill "$LXDE_PID" 2>/dev/null
  [ -n "$XVFB_PID" ] && kill "$XVFB_PID" 2>/dev/null
  [ -n "$LXDE_PID" ] && wait "$LXDE_PID" 2>/dev/null
  [ -n "$XVFB_PID" ] && wait "$XVFB_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

install -d -o oryx -g oryx \
  /home/oryx/.cache \
  /home/oryx/.config \
  /home/oryx/Desktop \
  /home/oryx/.local/share/applications

touch /home/oryx/.Xauthority
chown oryx:oryx /home/oryx/.Xauthority
rm -f /tmp/.X2-lock

/usr/bin/Xvfb "$DISPLAY_ID" -screen 0 "$GEOMETRY" -nolisten tcp -noreset -ac &
XVFB_PID="$!"

for _ in $(seq 1 30); do
  kill -0 "$XVFB_PID" 2>/dev/null || {
    echo "Xvfb s'est arrêté avant l'ouverture de l'écran :2" >&2
    exit 1
  }
  [ -S "$X_SOCKET" ] && break
  sleep 1
done

[ -S "$X_SOCKET" ] || {
  echo "L'écran virtuel :2 n'est pas disponible après 30 secondes" >&2
  exit 1
}

/usr/sbin/runuser -u oryx -- env \
  HOME=/home/oryx USER=oryx LOGNAME=oryx \
  DISPLAY="$DISPLAY_ID" XAUTHORITY=/home/oryx/.Xauthority \
  /usr/bin/dbus-run-session -- /usr/bin/startlxde &
LXDE_PID="$!"

wait -n "$XVFB_PID" "$LXDE_PID"
