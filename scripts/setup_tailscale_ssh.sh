#!/usr/bin/env bash
set -Eeuo pipefail

TAILSCALE_HOSTNAME="${TAILSCALE_HOSTNAME:-etr-core}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez ce script avec sudo." >&2
  exit 1
fi

case "$TAILSCALE_HOSTNAME" in
  ""|*[!A-Za-z0-9-]*)
    echo "Nom Tailscale invalide: $TAILSCALE_HOSTNAME" >&2
    exit 2
    ;;
esac

if ! command -v curl >/dev/null 2>&1 || [ ! -r /etc/ssl/certs/ca-certificates.crt ]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates
fi

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Installation de Tailscale depuis le dépôt officiel..."
  curl -fsSL https://tailscale.com/install.sh | sh
fi

systemctl enable --now tailscaled.service

backend_state() {
  tailscale status --json 2>/dev/null |
    python3 -c 'import json, sys; print(json.load(sys.stdin).get("BackendState", ""))' \
      2>/dev/null || true
}

if [ "$(backend_state)" = "Running" ]; then
  echo "Le Raspberry est déjà connecté à Tailscale; activation de Tailscale SSH..."
  tailscale set --ssh
else
  echo
  echo "Une URL d’authentification va s’afficher."
  echo "Ouvrez-la sur votre PC et connectez-vous au compte Tailscale choisi."
  echo
  tailscale up --ssh --hostname="$TAILSCALE_HOSTNAME"
fi

if [ "$(backend_state)" != "Running" ]; then
  echo "Tailscale n’est pas encore connecté. Relancez le script après l’authentification." >&2
  exit 3
fi

TAILSCALE_IPV4="$(tailscale ip -4 2>/dev/null | sed -n '1p')"

echo
echo "Tailscale SSH est actif."
echo "Adresse Tailscale : ${TAILSCALE_IPV4:-indisponible}"
if [ -n "$TAILSCALE_IPV4" ]; then
  echo "Terminal       : ssh oryx@$TAILSCALE_IPV4"
  echo "Copier un fichier : scp fichier.txt oryx@$TAILSCALE_IPV4:/home/oryx/"
fi
echo
echo "Le PC doit être connecté au même réseau Tailscale."
echo "Aucun port entrant n’a été ouvert sur la box Internet."
