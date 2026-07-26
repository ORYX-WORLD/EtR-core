#!/usr/bin/env bash
set -euo pipefail

BOOT_DIR=/boot/firmware
if [ ! -d "$BOOT_DIR" ]; then
  BOOT_DIR=/boot
fi

SERIAL=$(tr -d '\0' </proc/device-tree/serial-number 2>/dev/null || true)
if [ -z "$SERIAL" ]; then
  SERIAL=$(awk -F ': ' '/^Serial/ {print $2}' /proc/cpuinfo | tail -n 1)
fi
if [ -z "$SERIAL" ]; then
  SERIAL=$(tr -d '-' </etc/machine-id)
fi
DEVICE_SUFFIX=${SERIAL: -8}
DEVICE_ID="etr-${DEVICE_SUFFIX,,}"

if ! id oryx >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash oryx
  passwd --lock oryx
fi
EXTRA_GROUPS=()
for group in sudo video audio render input netdev; do
  getent group "$group" >/dev/null && EXTRA_GROUPS+=("$group")
done
if [ "${#EXTRA_GROUPS[@]}" -gt 0 ]; then
  usermod --append --groups "$(IFS=,; echo "${EXTRA_GROUPS[*]}")" oryx
fi

install -d -m 700 -o oryx -g oryx /home/oryx/.ssh
if [ -s "$BOOT_DIR/etr-authorized-key.pub" ]; then
  install -m 600 -o oryx -g oryx "$BOOT_DIR/etr-authorized-key.pub" /home/oryx/.ssh/authorized_keys
fi
printf 'oryx ALL=(ALL) NOPASSWD: ALL\n' >/etc/sudoers.d/90-etr-provisioning
chmod 440 /etc/sudoers.d/90-etr-provisioning

hostnamectl set-hostname "$DEVICE_ID"
sed -i "s/^127\\.0\\.1\\.1.*/127.0.1.1\t$DEVICE_ID/" /etc/hosts

install -d -m 700 /etc/etr-core
printf 'ETR_INSTALLATION_ID=%s\nETR_HARDWARE_SERIAL=%s\n' "$DEVICE_ID" "$SERIAL" \
  >/etc/etr-core/device.env
chmod 600 /etc/etr-core/device.env

cat >/usr/local/sbin/etr-bootstrap-online <<'BOOTSTRAP'
#!/usr/bin/env bash
set -euo pipefail

MARKER=/var/lib/etr-core/provisioned
if [ -f "$MARKER" ]; then
  systemctl disable etr-bootstrap-online.service
  exit 0
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates

INSTALL_DIR=/home/oryx/EtR-core
if [ ! -d "$INSTALL_DIR/.git" ]; then
  sudo -u oryx -H git clone https://github.com/ORYX-WORLD/EtR-core.git "$INSTALL_DIR"
else
  sudo -u oryx -H git -C "$INSTALL_DIR" fetch origin main
  sudo -u oryx -H git -C "$INSTALL_DIR" reset --hard origin/main
fi

sudo -u oryx -H bash "$INSTALL_DIR/src/deploy/raspi/setup_etr.sh"
install -d -m 700 -o oryx -g oryx /var/lib/etr-core
touch "$MARKER"
systemctl disable etr-bootstrap-online.service
BOOTSTRAP
chmod 755 /usr/local/sbin/etr-bootstrap-online

cat >/etc/systemd/system/etr-bootstrap-online.service <<'UNIT'
[Unit]
Description=Provisionnement initial EtR
Wants=network-online.target
After=network-online.target
ConditionPathExists=!/var/lib/etr-core/provisioned

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/etr-bootstrap-online
Restart=on-failure
RestartSec=30
TimeoutStartSec=30min

[Install]
WantedBy=multi-user.target
UNIT
systemctl enable etr-bootstrap-online.service

CMDLINE="$BOOT_DIR/cmdline.txt"
if [ -f "$CMDLINE" ]; then
  sed -i \
    -e 's# systemd.run=/boot/firmware/etr-firstboot.sh##g' \
    -e 's# systemd.run_success_action=reboot##g' \
    -e 's# systemd.unit=kernel-command-line.target##g' \
    "$CMDLINE"
fi
rm -f "$BOOT_DIR/etr-authorized-key.pub"
sync
