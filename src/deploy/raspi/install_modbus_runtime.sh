#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR=${ETR_INSTALL_DIR:-/home/oryx/EtR-core}
RUNTIME=/opt/etr-core-modbus/3.6.6
if ! PYTHONPATH="$RUNTIME" python3 -c 'import pymodbus, serial; assert pymodbus.__version__ == "3.6.6"; assert serial.VERSION == "3.5"' >/dev/null 2>&1; then
  sudo install -d -m 755 "$RUNTIME"
  sudo "${INSTALL_DIR}/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir --target "$RUNTIME" pymodbus==3.6.6 pyserial==3.5
fi
PYTHONPATH="$RUNTIME" python3 -c 'import pymodbus, serial; assert pymodbus.__version__ == "3.6.6"; assert serial.VERSION == "3.5"'
