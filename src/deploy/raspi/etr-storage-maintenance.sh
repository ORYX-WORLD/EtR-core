#!/usr/bin/env bash
# Entretien léger de l'espace disque du Raspberry EtR.
# Chromium peut accumuler des fichiers de télémétrie BrowserMetrics.
set +e

for root in \
  /home/oryx/.config/chromium \
  /home/oryx/.cache/etr-kiosk-chromium
do
  [ -d "$root" ] || continue
  find "$root" -xdev -type f -path '*/BrowserMetrics/*' -delete 2>/dev/null || true
  find "$root" -maxdepth 2 -type f -name '.org.chromium.Chromium.*' -delete 2>/dev/null || true
done
