#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=${ETR_INSTALL_DIR:-/home/oryx/EtR-core}
CONFIG_DIR=/etc/etr-core
CONFIG_FILE=${CONFIG_DIR}/sensors.json
STATE_DIR=/var/lib/etr-core
SPI_REBOOT_MARKER=${STATE_DIR}/spi-reboot-requested.boot-id
SERVICE=etr-sensor-acquisition.service

if [ ! -f "${INSTALL_DIR}/src/sensor_acquisition.py" ] || [ ! -f "${INSTALL_DIR}/src/ads1263.py" ]; then
  echo "Sources d'acquisition ADS1263 absentes dans ${INSTALL_DIR}" >&2
  exit 2
fi

sudo apt-get update
sudo apt-get install -y python3-lgpio python3-spidev

# Activer SPI par l'outil officiel et verrouiller aussi le paramètre persistant.
# Raspberry Pi OS Bookworm utilise /boot/firmware/config.txt ; les versions plus
# anciennes utilisent /boot/config.txt.
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_spi 0
fi
boot_config=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
  if [ -f "$candidate" ]; then
    boot_config=$candidate
    break
  fi
done
if [ -n "$boot_config" ]; then
  if sudo grep -Eq '^[[:space:]]*dtparam=spi=(off|0)[[:space:]]*$' "$boot_config"; then
    sudo sed -i -E 's/^[[:space:]]*dtparam=spi=(off|0)[[:space:]]*$/dtparam=spi=on/' "$boot_config"
  fi
  if ! sudo grep -Eq '^[[:space:]]*dtparam=spi=on([[:space:]]|$)' "$boot_config"; then
    printf '\n%s\n' 'dtparam=spi=on' | sudo tee -a "$boot_config" >/dev/null
  fi
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

# L'écran SPI peut occuper CS0 et ne laisser disponible que /dev/spidev0.1.
# Le driver ADS1263 utilise son propre CS sur GPIO22 (no_cs), donc n'importe
# quel nœud spidev du bus 0 convient pour MOSI/MISO/SCLK.
spi_node=""
for candidate in /dev/spidev0.0 /dev/spidev0.1; do
  if [ -c "$candidate" ]; then
    spi_node=$candidate
    break
  fi
done

if [ -z "$spi_node" ]; then
  current_boot_id=$(cat /proc/sys/kernel/random/boot_id)
  previous_boot_id=""
  if sudo test -s "${SPI_REBOOT_MARKER}"; then
    previous_boot_id=$(sudo cat "${SPI_REBOOT_MARKER}")
  fi
  if [ -n "$previous_boot_id" ] && [ "$previous_boot_id" != "$current_boot_id" ]; then
    echo "SPI_UNAVAILABLE_AFTER_REBOOT: aucun /dev/spidev0.* malgré dtparam=spi=on" >&2
    exit 43
  fi
  printf '%s\n' "$current_boot_id" | sudo tee "${SPI_REBOOT_MARKER}" >/dev/null
  sudo chown oryx:oryx "${SPI_REBOOT_MARKER}"
  sudo chmod 600 "${SPI_REBOOT_MARKER}"
  echo "SPI_REBOOT_REQUIRED: aucun /dev/spidev0.* après activation de SPI" >&2
  exit 42
fi

sudo rm -f "${SPI_REBOOT_MARKER}"
spi_device=${spi_node##*.}
CONFIG_FILE="$CONFIG_FILE" SPI_DEVICE="$spi_device" sudo -E python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["CONFIG_FILE"])
data = json.loads(path.read_text(encoding="utf-8"))
adc = data.setdefault("adc", {})
adc["bus"] = 0
adc["device"] = int(os.environ["SPI_DEVICE"])
temporary = path.with_suffix(".json.tmp")
temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary.replace(path)
PY
sudo chown root:oryx "${CONFIG_FILE}"
sudo chmod 640 "${CONFIG_FILE}"

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
