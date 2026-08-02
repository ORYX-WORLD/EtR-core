#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=${ETR_INSTALL_DIR:-/home/oryx/EtR-core}
CONFIG_DIR=/etc/etr-core
CONFIG_FILE=${CONFIG_DIR}/sensors.json
STATE_DIR=/var/lib/etr-core
SPI_REBOOT_MARKER=${STATE_DIR}/spi-cs2-reboot-requested.boot-id
SERVICE=etr-sensor-acquisition.service
OVERLAY_NAME=etr-ads1263-spi0-cs2
OVERLAY_SOURCE=${INSTALL_DIR}/src/deploy/raspi/${OVERLAY_NAME}-overlay.dts

for source in \
  "${INSTALL_DIR}/src/sensor_acquisition.py" \
  "${INSTALL_DIR}/src/sensor_acquisition_runtime.py" \
  "${INSTALL_DIR}/src/ads1263.py"; do
  if [ ! -f "$source" ]; then
    echo "Source d'acquisition ADS1263 absente : $source" >&2
    exit 2
  fi
done
if [ ! -f "${OVERLAY_SOURCE}" ]; then
  echo "Overlay ADS1263 absent : ${OVERLAY_SOURCE}" >&2
  exit 2
fi

sudo apt-get update
sudo apt-get install -y device-tree-compiler python3-lgpio python3-spidev
if ! command -v pinctrl >/dev/null 2>&1; then
  echo "Commande pinctrl absente : impossible de libérer RESET/GPIO18" >&2
  exit 2
fi

# Raspberry Pi OS Bookworm utilise /boot/firmware ; les versions plus anciennes
# utilisent /boot. L'overlay tft35a occupe SPI0.0 et SPI0.1. L'overlay EtR,
# chargé après lui, ajoute SPI0.2 et utilise GPIO22 comme troisième chip-select.
boot_config=""
overlay_dir=""
if [ -f /boot/firmware/config.txt ] && [ -d /boot/firmware/overlays ]; then
  boot_config=/boot/firmware/config.txt
  overlay_dir=/boot/firmware/overlays
elif [ -f /boot/config.txt ] && [ -d /boot/overlays ]; then
  boot_config=/boot/config.txt
  overlay_dir=/boot/overlays
else
  echo "Partition de démarrage Raspberry Pi introuvable" >&2
  exit 2
fi

if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_spi 0
fi
if sudo grep -Eq '^[[:space:]]*dtparam=spi=(off|0)[[:space:]]*$' "$boot_config"; then
  sudo sed -i -E 's/^[[:space:]]*dtparam=spi=(off|0)[[:space:]]*$/dtparam=spi=on/' "$boot_config"
fi
if ! sudo grep -Eq '^[[:space:]]*dtparam=spi=on([[:space:]]|$)' "$boot_config"; then
  printf '\n%s\n' 'dtparam=spi=on' | sudo tee -a "$boot_config" >/dev/null
fi

compiled_overlay=$(mktemp)
trap 'rm -f "$compiled_overlay"' EXIT
dtc -@ -I dts -O dtb -o "$compiled_overlay" "$OVERLAY_SOURCE"
sudo install -m 644 "$compiled_overlay" "${overlay_dir}/${OVERLAY_NAME}.dtbo"

if ! sudo grep -Eq "^[[:space:]]*dtoverlay=${OVERLAY_NAME}([,:[:space:]]|$)" "$boot_config"; then
  printf '\n# EtR : troisième chip-select SPI0 pour le HAT ADS1263\n%s\n' \
    "dtoverlay=${OVERLAY_NAME}" | sudo tee -a "$boot_config" >/dev/null
fi

sudo modprobe spi_bcm2835 2>/dev/null || true
sudo modprobe spidev 2>/dev/null || true
sudo udevadm settle 2>/dev/null || true
sudo usermod -a -G spi,gpio oryx
sudo install -d -m 750 -o root -g oryx "${CONFIG_DIR}"
sudo install -d -m 700 -o oryx -g oryx "${STATE_DIR}"

if [ ! -f "${CONFIG_FILE}" ]; then
  sudo install -m 640 -o root -g oryx "${INSTALL_DIR}/config/sensors-home-lab.json" "${CONFIG_FILE}"
fi
sudo python3 -m json.tool "${CONFIG_FILE}" >/dev/null
sudo install -m 644 "${INSTALL_DIR}/src/deploy/raspi/${SERVICE}" "/etc/systemd/system/${SERVICE}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE}"

# Le nœud SPI0.2 n'apparaît qu'après le redémarrage qui charge le nouvel overlay.
spi_node=/dev/spidev0.2
if [ ! -c "$spi_node" ]; then
  current_boot_id=$(cat /proc/sys/kernel/random/boot_id)
  previous_boot_id=""
  if sudo test -s "${SPI_REBOOT_MARKER}"; then
    previous_boot_id=$(sudo cat "${SPI_REBOOT_MARKER}")
  fi
  if [ -n "$previous_boot_id" ] && [ "$previous_boot_id" != "$current_boot_id" ]; then
    echo "SPI_CS2_UNAVAILABLE_AFTER_REBOOT: ${spi_node} absent malgré l'overlay ${OVERLAY_NAME}" >&2
    exit 43
  fi
  printf '%s\n' "$current_boot_id" | sudo tee "${SPI_REBOOT_MARKER}" >/dev/null
  sudo chown oryx:oryx "${SPI_REBOOT_MARKER}"
  sudo chmod 600 "${SPI_REBOOT_MARKER}"
  echo "SPI_REBOOT_REQUIRED: ${spi_node} sera créé après chargement de l'overlay ${OVERLAY_NAME}" >&2
  exit 42
fi

sudo rm -f "${SPI_REBOOT_MARKER}" "${STATE_DIR}/spi-reboot-requested.boot-id"
CONFIG_FILE="$CONFIG_FILE" sudo -E python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["CONFIG_FILE"])
data = json.loads(path.read_text(encoding="utf-8"))
adc = data.setdefault("adc", {})
adc.update(
    {
        "bus": 0,
        "device": 2,
        "manual_chip_select": False,
        "use_data_ready_gpio": False,
        "use_hardware_reset_gpio": False,
    }
)
temporary = path.with_suffix(".json.tmp")
temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary.replace(path)
PY
sudo chown root:oryx "${CONFIG_FILE}"
sudo chmod 640 "${CONFIG_FILE}"

# L'écran laisse GPIO18 en entrée avec pull-down, ce qui maintient RESET actif.
# Le service répète cette préparation avant chaque démarrage ; l'installateur la
# réalise aussi immédiatement pour le premier essai sans redémarrage.
sudo pinctrl set 18 op dh
sleep 0.3
sudo chown oryx:oryx "${STATE_DIR}"
sudo chmod 700 "${STATE_DIR}"

sudo systemctl restart "${SERVICE}"
for attempt in $(seq 1 45); do
  if sudo systemctl is-active --quiet "${SERVICE}" && [ -s "${STATE_DIR}/telemetry.json" ]; then
    break
  fi
  sleep 2
done

if ! sudo systemctl is-active --quiet "${SERVICE}"; then
  sudo systemctl status "${SERVICE}" --no-pager -l || true
  sudo journalctl -u "${SERVICE}" -n 120 --no-pager || true
  exit 3
fi

sudo chown oryx:oryx "${STATE_DIR}/telemetry.json"
sudo chmod 600 "${STATE_DIR}/telemetry.json"
sudo systemctl restart etr.service etr-dashboard.service 2>/dev/null || true

echo "Acquisition ADS1263 installée et service actif via ${spi_node}."