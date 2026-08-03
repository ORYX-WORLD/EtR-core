#!/usr/bin/env bash
# Lance le bureau EtR sur l'écran SPI lorsqu'il existe, sinon sur un écran
# virtuel headless accessible uniquement par le relais VNC local.
set -Eeuo pipefail

DISPLAY_ID=":1"
X_SOCKET="/tmp/.X11-unix/X1"
DISPLAY_PID=""
LXDE_PID=""
REPO_DIR="/home/oryx/EtR-core"
STATE_DIR="/run/etr-core"
STATE_FILE="$STATE_DIR/display-mode.json"
DISPLAY_MODE="unknown"

cleanup() {
  set +e
  [ -n "$LXDE_PID" ] && kill "$LXDE_PID" 2>/dev/null
  [ -n "$DISPLAY_PID" ] && kill "$DISPLAY_PID" 2>/dev/null
  [ -n "$LXDE_PID" ] && wait "$LXDE_PID" 2>/dev/null
  [ -n "$DISPLAY_PID" ] && wait "$DISPLAY_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

install -d -o oryx -g oryx \
  /home/oryx/.cache \
  /home/oryx/.config \
  /home/oryx/Desktop \
  /home/oryx/.local/share/applications
install -d -m 755 -o oryx -g oryx "$STATE_DIR"

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
# elle n'est accessible qu'après retour au bureau Linux local.
if [ -f "$REPO_DIR/src/deploy/raspi/etr-sd-factory.desktop" ]; then
  install -m 755 -o oryx -g oryx \
    "$REPO_DIR/src/deploy/raspi/etr-sd-factory.desktop" \
    /home/oryx/Desktop/Creer-une-carte-EtR.desktop
  install -m 644 -o oryx -g oryx \
    "$REPO_DIR/src/deploy/raspi/etr-sd-factory.desktop" \
    /home/oryx/.local/share/applications/etr-sd-factory.desktop
fi

touch /home/oryx/.Xauthority
chown oryx:oryx /home/oryx/.Xauthority
rm -f /tmp/.X1-lock "$X_SOCKET"

if [ -e /dev/fb1 ]; then
  DISPLAY_MODE="spi"
  /usr/lib/xorg/Xorg "$DISPLAY_ID" vt2 -keeptty -nolisten tcp -noreset -ac &
  DISPLAY_PID="$!"
elif [ -x /usr/bin/Xvfb ]; then
  DISPLAY_MODE="headless-xvfb"
  /usr/bin/Xvfb "$DISPLAY_ID" \
    -screen 0 1280x720x24 \
    -nolisten tcp \
    -noreset \
    -ac &
  DISPLAY_PID="$!"
else
  # Repli pour une ancienne image EtR : le premier démarrage force aussi une
  # sortie HDMI virtuelle. Xorg peut ainsi fournir :1 même sans écran branché.
  DISPLAY_MODE="headless-xorg"
  /usr/lib/xorg/Xorg "$DISPLAY_ID" vt2 -keeptty -nolisten tcp -noreset -ac &
  DISPLAY_PID="$!"
fi

for _ in $(seq 1 45); do
  kill -0 "$DISPLAY_PID" 2>/dev/null || {
    echo "Le serveur d'affichage EtR s'est arrêté avant l'ouverture de :1" >&2
    exit 1
  }
  [ -S "$X_SOCKET" ] && break
  sleep 1
done

[ -S "$X_SOCKET" ] || {
  echo "L'écran EtR :1 n'est pas disponible après 45 secondes" >&2
  exit 1
}

cat > "$STATE_FILE.tmp" <<EOF
{
  "display": ":1",
  "mode": "$DISPLAY_MODE",
  "physical_screen": $([ "$DISPLAY_MODE" = "spi" ] && echo true || echo false),
  "resolution": "$([ "$DISPLAY_MODE" = "spi" ] && echo automatic || echo 1280x720)"
}
EOF
chown oryx:oryx "$STATE_FILE.tmp"
chmod 644 "$STATE_FILE.tmp"
mv "$STATE_FILE.tmp" "$STATE_FILE"

/usr/sbin/runuser -u oryx -- env \
  HOME=/home/oryx USER=oryx LOGNAME=oryx \
  DISPLAY="$DISPLAY_ID" XAUTHORITY=/home/oryx/.Xauthority \
  /usr/bin/dbus-run-session -- /usr/bin/startlxde &
LXDE_PID="$!"

wait -n "$DISPLAY_PID" "$LXDE_PID"
