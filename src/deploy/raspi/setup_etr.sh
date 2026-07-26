#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/ORYX-WORLD/EtR-core.git"
INSTALL_DIR="/home/oryx/EtR-core"
ENV_FILE="/etc/etr-core/firebase-bridge.env"

sudo apt update
sudo apt install -y \
  git curl python3-venv python3-pip network-manager psmisc \
  xserver-xorg xserver-xorg-video-fbdev xinit lxde-core dbus-x11 \
  chromium netcat-openbsd x11vnc

# Cloner ou actualiser le dépôt officiel.
if [ ! -d "$INSTALL_DIR/.git" ]; then
  git clone "$REPO_URL" "$INSTALL_DIR"
else
  git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
  git -C "$INSTALL_DIR" fetch origin main
  git -C "$INSTALL_DIR" pull --ff-only origin main
fi
cd "$INSTALL_DIR"

# Environnement virtuel unique, avec dépendances du cœur et du dashboard versionné.
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install -r dashboard/requirements.txt

# Répertoires d'état locaux avant activation des sandbox systemd.
sudo install -d -m 700 -o oryx -g oryx /var/lib/etr-core
sudo install -d -m 750 -o root -g oryx /etc/etr-core
sudo touch "$ENV_FILE"
sudo chown root:oryx "$ENV_FILE"
sudo chmod 640 "$ENV_FILE"
for state_file in \
  /var/lib/etr-core/firebase-auth.json \
  /var/lib/etr-core/enrollment.json \
  /var/lib/etr-core/telemetry.json \
  /var/lib/etr-core/remote-screen-auth.json; do
  if [ -f "$state_file" ]; then
    sudo chown oryx:oryx "$state_file"
    sudo chmod 600 "$state_file"
  fi
done

# Si la passerelle WSS est déjà configurée, dériver automatiquement l'URL HTTPS
# d'enrôlement sans stocker une seconde origine manuellement.
remote_gateway=$(sudo sed -n 's/^ETR_REMOTE_GATEWAY_WSS=//p' "$ENV_FILE" | tail -n 1)
if [ -n "$remote_gateway" ] && ! sudo grep -q '^FIREBASE_ENROLLMENT_URL=.' "$ENV_FILE"; then
  enrollment_origin=${remote_gateway/wss:\/\//https:\/\/}
  enrollment_origin=${enrollment_origin%%\?*}
  enrollment_origin=${enrollment_origin%/device}
  enrollment_origin=${enrollment_origin%/}
  printf '%s\n' "FIREBASE_ENROLLMENT_URL=${enrollment_origin}/api/enrollment" | sudo tee -a "$ENV_FILE" >/dev/null
fi

# NetworkManager gère Ethernet, les profils Wi-Fi et le hotspot temporaire.
sudo systemctl enable NetworkManager.service

# Services principaux. L'ancienne unité dashboard éventuellement installée sous
# /opt/etr/dashboard est remplacée par l'unité versionnée dans ce dépôt.
sudo systemctl stop etr-dashboard.service etr-firebase-bridge.service 2>/dev/null || true
sudo install -m 644 src/deploy/etr.service /etc/systemd/system/etr.service
sudo install -m 644 src/deploy/raspi/etr-dashboard.service /etc/systemd/system/etr-dashboard.service
sudo install -m 644 src/deploy/raspi/etr-firebase-bridge.service /etc/systemd/system/etr-firebase-bridge.service
sudo install -m 644 src/deploy/raspi/etr-wifi-portal.service /etc/systemd/system/etr-wifi-portal.service

# Affichage SPI et kiosque.
sudo install -m 755 src/deploy/raspi/start_spi_desktop.sh /usr/local/bin/start_spi_desktop.sh
sudo install -m 755 src/deploy/raspi/etr-kiosk.sh /usr/local/bin/etr-kiosk.sh
sudo install -m 644 src/deploy/raspi/spi-desktop.service /etc/systemd/system/spi-desktop.service
sudo install -m 755 src/deploy/raspi/etr-disable-blanking.sh /usr/local/bin/etr-disable-blanking.sh
sudo install -d -m 755 /etc/systemd/system/spi-desktop.service.d
sudo install -m 644 src/deploy/raspi/spi-desktop.service.d/blanking.conf /etc/systemd/system/spi-desktop.service.d/blanking.conf
sudo install -m 644 src/deploy/raspi/etr-kiosk.service /etc/systemd/system/etr-kiosk.service

# Écran distant : VNC reste local au Raspberry et le relais est uniquement sortant.
sudo install -m 644 src/deploy/raspi/etr-vnc.service /etc/systemd/system/etr-vnc.service
sudo install -m 644 src/deploy/raspi/etr-remote-screen.service /etc/systemd/system/etr-remote-screen.service

# Protection de l'espace disque et jeton distinct du relais écran.
sudo systemctl stop etr-remote-screen.service 2>/dev/null || true
sudo install -d -m 755 -o oryx -g oryx /home/oryx/.local/bin
sudo rm -f /var/lib/etr-core/remote-screen-auth.tmp
sudo install -m 755 src/deploy/raspi/etr-storage-maintenance.sh /home/oryx/.local/bin/etr-storage-maintenance.sh
echo '*/15 * * * * oryx /home/oryx/.local/bin/etr-storage-maintenance.sh' | \
  sudo tee /etc/cron.d/etr-storage-maintenance >/dev/null
sudo chmod 644 /etc/cron.d/etr-storage-maintenance

# Une ancienne exécution manuelle peut conserver le port 8090 ou le profil Chromium.
sudo systemctl stop etr-kiosk.service etr-wifi-portal.service 2>/dev/null || true
sudo pkill -f 'wifi_portal.py' 2>/dev/null || true
sudo fuser -k 8090/tcp 2>/dev/null || true
sudo pkill -TERM -u oryx -f '[c]hromium.*etr-kiosk-chromium' 2>/dev/null || true
sleep 2
sudo pkill -KILL -u oryx -f '[c]hromium.*etr-kiosk-chromium' 2>/dev/null || true
sudo rm -f /home/oryx/.cache/etr-kiosk-chromium/SingletonCookie \
            /home/oryx/.cache/etr-kiosk-chromium/SingletonLock \
            /home/oryx/.cache/etr-kiosk-chromium/SingletonSocket
sudo systemctl reset-failed etr-dashboard.service etr-firebase-bridge.service etr-wifi-portal.service etr-kiosk.service 2>/dev/null || true

sudo systemctl daemon-reload
sudo systemctl enable etr.service etr-dashboard.service etr-wifi-portal.service spi-desktop.service etr-kiosk.service
sudo systemctl restart etr.service

api_ready=false
for attempt in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:8080/healthz >/dev/null; then
    api_ready=true
    break
  fi
  sleep 2
done
if [ "$api_ready" != true ]; then
  sudo systemctl status etr.service --no-pager || true
  sudo journalctl -u etr.service -n 80 --no-pager || true
  exit 1
fi

# Le bridge démarre dès que la configuration Firebase minimale existe. Il reste
# actif en attente d'association et génère lui-même le code visible localement.
if sudo grep -q '^FIREBASE_API_KEY=.' "$ENV_FILE" && \
   sudo grep -q '^FIREBASE_DATABASE_URL=.' "$ENV_FILE"; then
  sudo systemctl enable etr-firebase-bridge.service
  sudo systemctl restart etr-firebase-bridge.service
else
  sudo systemctl disable --now etr-firebase-bridge.service 2>/dev/null || true
  echo "Bridge Firebase non activé : FIREBASE_API_KEY et FIREBASE_DATABASE_URL sont requis dans $ENV_FILE"
fi

sudo systemctl restart etr-dashboard.service
dashboard_ready=false
for attempt in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null; then
    dashboard_ready=true
    break
  fi
  sleep 2
done
if [ "$dashboard_ready" != true ]; then
  sudo systemctl status etr-dashboard.service --no-pager || true
  sudo journalctl -u etr-dashboard.service -n 80 --no-pager || true
  exit 1
fi

sudo systemctl restart spi-desktop.service

# La passerelle distante est activée uniquement lorsqu'une URL WSS a été configurée.
remote_gateway=$(sudo sed -n 's/^ETR_REMOTE_GATEWAY_WSS=//p' "$ENV_FILE" | tail -n 1)
if [ -n "$remote_gateway" ]; then
  sudo systemctl stop etr-vnc.service 2>/dev/null || true
  sudo pkill -f '[x]11vnc.*rfbport 5901' 2>/dev/null || true
  sudo systemctl enable etr-vnc.service etr-remote-screen.service
  sudo systemctl restart etr-vnc.service
  sudo systemctl restart etr-remote-screen.service
else
  sudo systemctl disable --now etr-remote-screen.service etr-vnc.service 2>/dev/null || true
  echo "Passerelle distante non activée : définir ETR_REMOTE_GATEWAY_WSS dans $ENV_FILE"
fi

# Le portail doit être réellement joignable avant de démarrer le kiosque.
sudo systemctl start etr-wifi-portal.service
portal_ready=false
for attempt in $(seq 1 60); do
  if sudo systemctl is-active --quiet etr-wifi-portal.service && \
     curl -fsS http://127.0.0.1:8090/api/status >/dev/null; then
    portal_ready=true
    break
  fi
  sleep 2
done
if [ "$portal_ready" != true ]; then
  sudo systemctl status etr-wifi-portal.service --no-pager || true
  sudo journalctl -u etr-wifi-portal.service -n 80 --no-pager || true
  exit 1
fi

sudo systemctl reset-failed etr-kiosk.service 2>/dev/null || true
sudo systemctl start etr-kiosk.service

echo "OK. API EtR, bridge Firebase, enrôlement sécurisé, dashboard versionné, portail Wi-Fi tactile, écran SPI, kiosque et configuration d'écran distant sont installés."
echo "Un redémarrage est recommandé pour valider le parcours hors connexion."
