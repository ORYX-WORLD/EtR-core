#!/usr/bin/env bash
set -euo pipefail

repo=/home/oryx/EtR-core

# Installation idempotente du moteur séparé. La fenêtre peut ensuite être
# redémarrée sans interrompre une copie déjà lancée par ce moteur.
/usr/bin/install -m 755 "$repo/src/deploy/raspi/etr-sd-factory-cleanup.sh" /usr/local/bin/etr-sd-factory-cleanup
/usr/bin/install -m 644 "$repo/src/deploy/raspi/etr-sd-factory-worker.service" /etc/systemd/system/etr-sd-factory-worker.service
/usr/bin/systemctl daemon-reload
/usr/bin/systemctl reset-failed etr-sd-factory-worker.service etr-sd-factory.service 2>/dev/null || true
/usr/bin/systemctl restart etr-sd-factory.service
