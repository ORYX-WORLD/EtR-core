#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP_SCRIPT="$REPO_DIR/src/deploy/raspi/setup_etr.sh"

if [ "$(id -u)" -ne 0 ]; then
  echo "Exécutez ce script avec sudo."
  exit 1
fi

if [ ! -x "$SETUP_SCRIPT" ]; then
  echo "Installateur EtR introuvable : $SETUP_SCRIPT" >&2
  exit 1
fi

cat <<'NOTICE'
L'ancien bridge autonome sous /opt/etr-core est supprimé de l'architecture.
Le bridge Firebase est désormais installé depuis le dépôt EtR-core, exécuté sous
l'utilisateur non privilégié oryx et contrôlé par le déploiement physique complet.
NOTICE

# Supprimer seulement l'ancienne unité et son processus. Les fichiers de jetons
# restent dans /var/lib/etr-core et ne sont jamais effacés par cette migration.
systemctl disable --now etr-firebase-bridge.service 2>/dev/null || true
rm -f /etc/systemd/system/etr-firebase-bridge.service
systemctl daemon-reload

exec runuser -u oryx -- bash "$SETUP_SCRIPT"
