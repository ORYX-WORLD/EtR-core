#!/usr/bin/env bash
set -Eeuo pipefail

DISPLAY_ID=":2"
GEOMETRY="${ETR_REMOTE_DESKTOP_GEOMETRY:-1280x720x24}"
X_SOCKET="/tmp/.X11-unix/X2"
XVFB_PID=""
LXDE_PID=""
REMOTE_CONFIG_HOME="/home/oryx/.config/etr-remote-desktop"
REMOTE_CACHE_HOME="/home/oryx/.cache/etr-remote-desktop"
REMOTE_DATA_HOME="/home/oryx/.local/share/etr-remote-desktop"

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
  /home/oryx/.local/share/applications \
  "$REMOTE_CONFIG_HOME/lxsession/LXDE" \
  "$REMOTE_CACHE_HOME" \
  "$REMOTE_DATA_HOME"

# La session VNC est lancée par systemd et n'est donc pas une session graphique
# enregistrée dans systemd-logind. LXDE lance normalement lxpolkit, qui tente de
# retrouver sa session logind et affiche alors « No session for pid ... ».
# On utilise une configuration LXDE dédiée au bureau distant et on désactive
# uniquement cet agent polkit dans cette session. La session physique :1 reste
# inchangée et conserve son agent d'authentification habituel.
if [ -f /etc/xdg/lxsession/LXDE/desktop.conf ]; then
  sed 's/^polkit\/command=.*/polkit\/command=/' \
    /etc/xdg/lxsession/LXDE/desktop.conf \
    > "$REMOTE_CONFIG_HOME/lxsession/LXDE/desktop.conf"
else
  cat > "$REMOTE_CONFIG_HOME/lxsession/LXDE/desktop.conf" <<'EOF'
[Session]
window_manager=openbox
polkit/command=
EOF
fi

if [ -f /home/oryx/.config/lxsession/LXDE/autostart ]; then
  cp /home/oryx/.config/lxsession/LXDE/autostart \
    "$REMOTE_CONFIG_HOME/lxsession/LXDE/autostart"
elif [ -f /etc/xdg/lxsession/LXDE/autostart ]; then
  cp /etc/xdg/lxsession/LXDE/autostart \
    "$REMOTE_CONFIG_HOME/lxsession/LXDE/autostart"
fi

chown -R oryx:oryx "$REMOTE_CONFIG_HOME" "$REMOTE_CACHE_HOME" "$REMOTE_DATA_HOME"

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
  XDG_CONFIG_HOME="$REMOTE_CONFIG_HOME" \
  XDG_CACHE_HOME="$REMOTE_CACHE_HOME" \
  XDG_DATA_HOME="$REMOTE_DATA_HOME" \
  NO_AT_BRIDGE=1 \
  /usr/bin/dbus-run-session -- /usr/bin/startlxde &
LXDE_PID="$!"

wait -n "$XVFB_PID" "$LXDE_PID"
