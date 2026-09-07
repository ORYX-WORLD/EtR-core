#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'ETR_FACTORY_AUDIT_FAIL|%s|%s\n' "$1" "$2"
  exit 1
}

pass() {
  printf 'ETR_FACTORY_AUDIT_OK|%s|%s\n' "$1" "$2"
}

repo=/home/oryx/EtR-core
desktop=/home/oryx/Desktop/SD-V1.1.desktop
launcher=/usr/local/bin/etr-sd-factory-launch.sh
state=/var/lib/etr-core/sd-factory-state.json
request=/var/lib/etr-core/sd-factory-request.json

# This audit must remain read-only with respect to block devices.  In
# particular it deliberately contains no mount, umount, wipefs, sfdisk,
# mkfs, fsck, dd, rsync or systemctl start/restart operation.

source_device=$(findmnt -n -o SOURCE /)
source_parent=$(lsblk -ndo PKNAME "$source_device" 2>/dev/null || true)
if [ -n "$source_parent" ]; then
  source_disk="/dev/$source_parent"
else
  source_disk="$source_device"
fi
[ -b "$source_disk" ] || fail source_disk "disque systeme introuvable depuis $source_device"
pass source_disk "$source_disk"

[ -s "$desktop" ] || fail desktop "raccourci absent"
grep -qx 'Name=SD V1.1' "$desktop" || fail desktop "nom visible non conforme"
grep -qx 'Exec=sudo -n /usr/local/bin/etr-sd-factory-launch.sh' "$desktop" || fail desktop "launcher non conforme"
[ -x "$launcher" ] || fail launcher "launcher installe absent"
pass desktop "SD V1.1 -> launcher systeme"

for unit in etr-sd-factory.service etr-sd-factory-worker.service; do
  systemctl cat "$unit" >/dev/null 2>&1 || fail service "$unit absent"
done
systemctl show -p ExecStart --value etr-sd-factory.service | grep -q 'etr_sd_factory_resilient.py' \
  || fail service "interface resiliente non installee"
systemctl show -p ExecStart --value etr-sd-factory-worker.service | grep -q 'etr_sd_factory_worker.py' \
  || fail worker "moteur independant non installe"
pass service "interface et moteur independant installes"

if systemctl is-active --quiet etr-sd-factory-worker.service; then
  fail worker "moteur destructif actif pendant audit"
fi
if systemctl is-active --quiet etr-sd-factory-auto.service 2>/dev/null; then
  fail worker "ancien moteur automatique actif pendant audit"
fi
pass worker "aucun moteur d'ecriture actif"

if [ -e "$request" ]; then
  python3 - "$request" <<'PY' || fail request "requete persistante invalide ou active"
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
status = str(p.get("status") or "").lower()
if status not in {"", "cancelled", "failed", "ready", "completed"}:
    raise SystemExit(1)
PY
fi
pass request "aucune demande de fabrication executable"

if sudo -n test -s "$state"; then
  sudo -n python3 - "$state" <<'PY' || fail progress "etat de progression invalide"
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
value = float(p.get("progress_percent") or 0)
if not 0 <= value <= 100:
    raise SystemExit(1)
print("ETR_FACTORY_STATE|status=%s|progress=%s|stage=%s" % (
    p.get("status", ""), value, str(p.get("stage", "")).replace("|", "/")
))
PY
else
  printf 'ETR_FACTORY_STATE|status=absent|progress=0|stage=aucun travail\n'
fi
pass progress "etat persistant borne entre 0 et 100"

candidate_count=0
while IFS='|' read -r path type size tran rm mountpoints; do
  [ "$type" = disk ] || continue
  [ "$path" != "$source_disk" ] || continue
  if [ "$tran" = usb ] || [ "$rm" = 1 ]; then
    candidate_count=$((candidate_count + 1))
    [ -z "$mountpoints" ] || fail target "$path est monte sur $mountpoints"
    printf 'ETR_FACTORY_TARGET|path=%s|size=%s|tran=%s|rm=%s|mounted=no\n' "$path" "$size" "$tran" "$rm"
  fi
done < <(lsblk -dnpo PATH,TYPE,SIZE,TRAN,RM,MOUNTPOINTS --pairs | awk '
  {
    for (i=1;i<=NF;i++) { split($i,a,"="); gsub(/^\"|\"$/,"",a[2]); v[a[1]]=a[2] }
    print v["PATH"] "|" v["TYPE"] "|" v["SIZE"] "|" v["TRAN"] "|" v["RM"] "|" v["MOUNTPOINTS"]
    delete v
  }')
pass target "$candidate_count support(s) externe(s) non monte(s) detecte(s)"

[ -e /home/oryx/.Xauthority ] || fail display "XAUTHORITY absent"
DISPLAY=:1 XAUTHORITY=/home/oryx/.Xauthority xdpyinfo >/dev/null 2>&1 \
  || fail display "bureau physique :1 inaccessible"
DISPLAY=:2 XAUTHORITY=/home/oryx/.Xauthority xdpyinfo >/dev/null 2>&1 \
  || fail display "bureau distant :2 inaccessible"
pass display "bureaux :1 et :2 accessibles"

if systemctl is-active --quiet etr-sd-factory.service; then
  pgrep -af 'etr_sd_factory_(resilient|fast|py)' >/dev/null \
    || fail ui "service actif sans processus graphique"
  if command -v xwininfo >/dev/null 2>&1; then
    DISPLAY=:1 XAUTHORITY=/home/oryx/.Xauthority xwininfo -root -tree 2>/dev/null \
      | grep -Eiq 'Fabrique de cartes EtR|Créer une carte microSD EtR' \
      || fail ui "fenetre Fabrique non visible sur :1"
  fi
  pass ui "interface Fabrique active et visible sur :1"
else
  pass ui "interface inactive; aucun lancement effectue pendant audit"
fi

printf 'ETR_FACTORY_AUDIT_SUCCESS|all|controle non destructif termine\n'
