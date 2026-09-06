#!/usr/bin/env bash
# Lance Xorg sur l'écran MHS35 (/dev/fb1), puis LXDE sous l'utilisateur oryx.
set -Eeuo pipefail

DISPLAY_ID=":1"
X_SOCKET="/tmp/.X11-unix/X1"
XORG_PID=""
LXDE_PID=""
REPO_DIR="/home/oryx/EtR-core"

cleanup() {
  set +e
  [ -n "$LXDE_PID" ] && kill "$LXDE_PID" 2>/dev/null
  [ -n "$XORG_PID" ] && kill "$XORG_PID" 2>/dev/null
  [ -n "$LXDE_PID" ] && wait "$LXDE_PID" 2>/dev/null
  [ -n "$XORG_PID" ] && wait "$XORG_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

install -d -o oryx -g oryx \
  /home/oryx/.cache \
  /home/oryx/.config \
  /home/oryx/Desktop \
  /home/oryx/.local/share/applications

# Le bureau reste utilisable sans clavier : après avoir masqué le kiosque avec
# le bouton « Bureau Linux », ce raccourci relance l'interface EtR par l'API
# locale privilégiée du portail.
if [ -f "$REPO_DIR/src/deploy/raspi/etr-dashboard.desktop" ]; then
  install -m 755 -o oryx -g oryx \
    "$REPO_DIR/src/deploy/raspi/etr-dashboard.desktop" \
    /home/oryx/Desktop/Revenir-a-EtR.desktop
  install -m 644 -o oryx -g oryx \
    "$REPO_DIR/src/deploy/raspi/etr-dashboard.desktop" \
    /home/oryx/.local/share/applications/etr-dashboard.desktop
fi

# La fabrication de microSD est volontairement séparée de l'interface EtR :
# elle n'est accessible qu'après retour au bureau Linux local. Le nom de fichier
# du raccourci est versionné et constitue la référence unique de diagnostic.
if [ -f "$REPO_DIR/src/deploy/raspi/etr-sd-factory.desktop" ]; then
  rm -f /home/oryx/Desktop/Creer-une-carte-EtR.desktop \
        /home/oryx/Desktop/etr-sd-factory.desktop
  install -m 755 -o oryx -g oryx \
    "$REPO_DIR/src/deploy/raspi/etr-sd-factory.desktop" \
    /home/oryx/Desktop/SD-V1.1.desktop
  install -m 644 -o oryx -g oryx \
    "$REPO_DIR/src/deploy/raspi/etr-sd-factory.desktop" \
    /home/oryx/.local/share/applications/etr-sd-factory.desktop
fi

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
