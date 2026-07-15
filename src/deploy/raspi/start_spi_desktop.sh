#!/usr/bin/env bash
# Lance Xorg sur l'écran MHS35 (/dev/fb1), puis LXDE sous l'utilisateur oryx.
set -Eeuo pipefail

DISPLAY_ID=":1"
X_SOCKET="/tmp/.X11-unix/X1"
XORG_PID=""
LXDE_PID=""

cleanup() {
  set +e
  [ -n "$LXDE_PID" ] && kill "$LXDE_PID" 2>/dev/null
  [ -n "$XORG_PID" ] && kill "$XORG_PID" 2>/dev/null
  [ -n "$LXDE_PID" ] && wait "$LXDE_PID" 2>/dev/null
  [ -n "$XORG_PID" ] && wait "$XORG_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

install -d -o oryx -g oryx /home/oryx/.cache /home/oryx/.config
touch /home/oryx/.Xauthority
chown oryx:oryx /home/oryx/.Xauthority
rm -f /tmp/.X1-lock

/usr/lib/xorg/Xorg "$DISPLAY_ID" vt2 -keeptty -nolisten tcp -noreset -ac &
XORG_PID="$!"

for _ in $(seq 1 30); do
  kill -0 "$XORG_PID" 2>/dev/null || {
    echo "Xorg s'est arrêté avant l'ouverture de l'écran :1" >&2
    exit 1
  }
  [ -S "$X_SOCKET" ] && break
  sleep 1
done

[ -S "$X_SOCKET" ] || {
  echo "L'écran X :1 n'est pas disponible après 30 secondes" >&2
  exit 1
}

/usr/sbin/runuser -u oryx -- env \
  HOME=/home/oryx USER=oryx LOGNAME=oryx \
  DISPLAY="$DISPLAY_ID" XAUTHORITY=/home/oryx/.Xauthority \
  /usr/bin/dbus-run-session -- /usr/bin/startlxde &
LXDE_PID="$!"

wait -n "$XORG_PID" "$LXDE_PID"
