#!/usr/bin/env bash
# Entretien léger de l'espace disque du Raspberry EtR.
# Chromium peut accumuler des fichiers de télémétrie BrowserMetrics.
set +e

METRICS="/home/oryx/.config/chromium/BrowserMetrics"
find "$METRICS" -xdev -type f -delete 2>/dev/null || true
find /home/oryx/.config/chromium -maxdepth 1 -type f \
  -name '.org.chromium.Chromium.*' -delete 2>/dev/null || true
