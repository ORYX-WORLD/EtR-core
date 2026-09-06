#!/usr/bin/env bash
set -euo pipefail

repo=/home/oryx/EtR-core

# Ne jamais superposer le nouvel orchestrateur à l'ancienne relance provisoire.
if /usr/bin/systemctl is-active --quiet etr-sd-factory-auto.service 2>/dev/null; then
  /usr/bin/logger -t etr-sd-factory "Ancienne fabrication encore active; ouverture du nouvel orchestrateur refusée"
  exit 2
fi
/usr/bin/systemctl disable etr-sd-factory-auto.service 2>/dev/null || true
/usr/bin/rm -f /etc/systemd/system/etr-sd-factory-auto.service

# Résilience réseau permanente : EtR ne dépend jamais du SSID ni du nom d'une
# connexion. La connexion active est détectée dynamiquement. Le DNS DHCP reste
# prioritaire et des DNS de secours ne sont activés que si une résolution réelle
# de la passerelle EtR échoue.
if [ -s "$repo/src/deploy/raspi/etr-network-resilience.sh" ]; then
  /usr/bin/install -m 755 "$repo/src/deploy/raspi/etr-network-resilience.sh" /usr/local/bin/etr-network-resilience.sh
  /usr/bin/install -m 644 "$repo/src/deploy/raspi/etr-network-resilience.service" /etc/systemd/system/etr-network-resilience.service
  /usr/bin/install -m 644 "$repo/src/deploy/raspi/etr-network-resilience.timer" /etc/systemd/system/etr-network-resilience.timer
fi

# Installation idempotente du moteur séparé. La fenêtre peut ensuite être
# redémarrée sans interrompre une copie déjà lancée par ce moteur.
/usr/bin/install -m 755 "$repo/src/deploy/raspi/etr-sd-factory-cleanup.sh" /usr/local/bin/etr-sd-factory-cleanup
/usr/bin/install -m 644 "$repo/src/deploy/raspi/etr-sd-factory-worker.service" /etc/systemd/system/etr-sd-factory-worker.service
/usr/bin/systemctl daemon-reload
/usr/bin/systemctl enable --now etr-network-resilience.timer 2>/dev/null || true
/usr/bin/systemctl start etr-network-resilience.service 2>/dev/null || true
/usr/bin/systemctl reset-failed etr-sd-factory-worker.service etr-sd-factory.service 2>/dev/null || true
/usr/bin/systemctl restart etr-sd-factory.service
