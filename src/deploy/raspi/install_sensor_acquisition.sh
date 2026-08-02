#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=${ETR_INSTALL_DIR:-/home/oryx/EtR-core}
CONFIG_DIR=/etc/etr-core
CONFIG_FILE=${CONFIG_DIR}/sensors.json
STATE_DIR=/var/lib/etr-core
SERVICE=etr-sensor-acquisition.service

if [ ! -f "${INSTALL_DIR}/src/sensor_acquisition.py" ] || [ ! -f "${INSTALL_DIR}/src/ads1263.py" ]; then
  echo "Sources d'acquisition ADS1263 absentes dans ${INSTALL_DIR}" >&2
  exit 2
fi

sudo apt-get update
sudo apt-get install -y python3-lgpio python3-spidev

# Enable SPI persistently. If the device node is not already present, a reboot
# will be reported explicitly instead of pretending that acquisition works.
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_spi 0
fi
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

if [ ! -e /dev/spidev0.0 ]; then
  echo "SPI_REBOOT_REQUIRED: /dev/spidev0.0 absent après activation de SPI" >&2
  exit 42
fi

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

echo "Acquisition ADS1263 installée et service actif."
