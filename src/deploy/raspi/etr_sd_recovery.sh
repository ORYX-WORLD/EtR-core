#!/usr/bin/env bash
set -euo pipefail

REPORT_FILE=${ETR_SD_RECOVERY_REPORT:-/var/lib/etr-core/sd-recovery-last.json}
TARGET=${ETR_SD_RECOVERY_DEVICE:-}
MIN_BYTES=$((8 * 1024 * 1024 * 1024))
BOOT_SECTORS=$((512 * 1024 * 1024 / 512))
START_SECTOR=8192

log() { printf '%s\n' "$*"; }
fail() {
  local code=$1
  shift
  write_report "failure" "$code" "$*"
  printf 'ERREUR: %s\n' "$*" >&2
  exit 1
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

write_report() {
  local status=$1 code=$2 message=$3
  local now device size model serial medium_errors
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  device=${TARGET:-}
  size=$(lsblk -bdno SIZE "$device" 2>/dev/null || true)
  model=$(lsblk -dno MODEL "$device" 2>/dev/null | xargs || true)
  serial=$(udevadm info --query=property --name="$device" 2>/dev/null | sed -n 's/^ID_SERIAL_SHORT=//p' | head -n1)
  medium_errors=$(journalctl -k --since "-30 minutes" --no-pager 2>/dev/null | grep -E "critical medium error, dev ${device##*/}|I/O error, dev ${device##*/}" | tail -n 20 || true)
  install -d -m 700 "$(dirname "$REPORT_FILE")"
  NOW="$now" STATUS="$status" CODE="$code" MESSAGE="$message" DEVICE="$device" SIZE="$size" MODEL="$model" SERIAL="$serial" MEDIUM_ERRORS="$medium_errors" \
    python3 - <<'PY' > "${REPORT_FILE}.tmp"
import json, os
print(json.dumps({
    "checkedAt": os.environ.get("NOW"),
    "status": os.environ.get("STATUS"),
    "code": os.environ.get("CODE"),
    "message": os.environ.get("MESSAGE"),
    "device": os.environ.get("DEVICE"),
    "size": int(os.environ.get("SIZE") or 0),
    "model": os.environ.get("MODEL"),
    "serial": os.environ.get("SERIAL"),
    "kernelErrors": [line for line in os.environ.get("MEDIUM_ERRORS", "").splitlines() if line],
}, ensure_ascii=False, indent=2))
PY
  chmod 600 "${REPORT_FILE}.tmp"
  mv "${REPORT_FILE}.tmp" "$REPORT_FILE"
}

source_disk() {
  local src parent
  src=$(findmnt -n -o SOURCE /)
  while [ "$(lsblk -ndo TYPE "$src" 2>/dev/null || true)" != "disk" ]; do
    parent=$(lsblk -no PKNAME "$src" 2>/dev/null | head -n1)
    [ -n "$parent" ] || return 1
    src="/dev/$parent"
  done
  printf '%s\n' "$src"
}

select_target() {
  local source path type rm tran size
  source=$(source_disk) || fail "source_unknown" "Impossible d'identifier le disque système."
  if [ -n "$TARGET" ]; then
    [ "$TARGET" != "$source" ] || fail "unsafe_target" "Le disque système ne peut jamais être réparé par ce service."
    return
  fi
  local -a candidates=()
  while read -r path type rm tran size; do
    [ "$type" = "disk" ] || continue
    [ "$path" != "$source" ] || continue
    [ "$size" -ge "$MIN_BYTES" ] || continue
    if [ "$rm" = "1" ] || [ "$tran" = "usb" ]; then candidates+=("$path"); fi
  done < <(lsblk -bdnpo PATH,TYPE,RM,TRAN,SIZE)
  [ "${#candidates[@]}" -eq 1 ] || fail "target_ambiguous" "Le service exige exactement une microSD USB/amovible connectée."
  TARGET=${candidates[0]}
}

partition_path() {
  local disk=$1 number=$2
  if [[ "$disk" =~ [0-9]$ ]]; then printf '%sp%s\n' "$disk" "$number"; else printf '%s%s\n' "$disk" "$number"; fi
}

unmount_children() {
  while read -r name mountpoint; do
    [ -n "${mountpoint:-}" ] || continue
    umount -l "$name" 2>/dev/null || true
  done < <(lsblk -nrpo NAME,MOUNTPOINTS "$TARGET" | tac)
}

validate_target() {
  local type rm tran ro size source
  source=$(source_disk)
  [ "$TARGET" != "$source" ] || fail "unsafe_target" "Refus de toucher au disque système."
  type=$(lsblk -dno TYPE "$TARGET" 2>/dev/null | xargs)
  rm=$(lsblk -dno RM "$TARGET" 2>/dev/null | xargs)
  tran=$(lsblk -dno TRAN "$TARGET" 2>/dev/null | xargs)
  ro=$(lsblk -dno RO "$TARGET" 2>/dev/null | xargs)
  size=$(lsblk -bdno SIZE "$TARGET" 2>/dev/null | xargs)
  [ "$type" = "disk" ] || fail "invalid_target" "$TARGET n'est pas un disque entier."
  { [ "$rm" = "1" ] || [ "$tran" = "usb" ]; } || fail "not_removable" "$TARGET n'est pas identifié comme USB/amovible."
  [ "$ro" = "0" ] || fail "read_only" "La microSD ou son adaptateur est verrouillé en lecture seule."
  [ "${size:-0}" -ge "$MIN_BYTES" ] || fail "too_small" "La microSD fait moins de 8 Gio."
}

recent_bad_sectors() {
  journalctl -k --since "-2 hours" --no-pager 2>/dev/null \
    | sed -nE "s/.*critical medium error, dev ${TARGET##*/}, sector ([0-9]+).*/\1/p" \
    | sort -nu
}

repair_bad_sector_area() {
  local sector=$1 start count
  start=$(( sector > 65536 ? sector - 65536 : 0 ))
  count=131072
  log "Tentative de remappage contrôlé autour du secteur $sector..."
  if ! dd if=/dev/zero of="$TARGET" bs=512 seek="$start" count="$count" conv=fsync,notrunc status=none; then
    return 1
  fi
  blockdev --flushbufs "$TARGET" || true
  if ! dd if="$TARGET" of=/dev/null bs=512 skip="$start" count="$count" iflag=direct status=none; then
    return 1
  fi
}

recreate_filesystems() {
  local boot root layout sectors
  unmount_children
  wipefs -a "$TARGET"
  sectors=$BOOT_SECTORS
  layout=$(printf 'label: dos\nunit: sectors\n\nstart=%s, size=%s, type=c, bootable\nstart=%s, type=83\n' "$START_SECTOR" "$sectors" "$((START_SECTOR + sectors))")
  printf '%s' "$layout" | sfdisk --wipe always "$TARGET"
  partprobe "$TARGET" || true
  udevadm settle || true
  boot=$(partition_path "$TARGET" 1)
  root=$(partition_path "$TARGET" 2)
  for _ in $(seq 1 80); do [ -b "$boot" ] && [ -b "$root" ] && break; sleep 0.25; done
  [ -b "$boot" ] && [ -b "$root" ] || return 1
  mkfs.vfat -F 32 -n bootfs "$boot"
  mkfs.ext4 -F -L rootfs "$root"
  e2fsck -pf "$root" || [ "$?" -le 1 ]
}

main() {
  [ "$(id -u)" -eq 0 ] || fail "root_required" "Le service de récupération doit être exécuté par root."
  select_target
  validate_target
  unmount_children

  mapfile -t bad_sectors < <(recent_bad_sectors)
  if [ "${#bad_sectors[@]}" -gt 0 ]; then
    for sector in "${bad_sectors[@]}"; do
      repair_bad_sector_area "$sector" || fail "medium_error_persistent" "La mémoire flash reste illisible autour du secteur $sector. Carte non fiable à remplacer."
    done
  fi

  log "Recréation complète de la table de partitions et des systèmes de fichiers..."
  if ! recreate_filesystems; then
    if journalctl -k --since "-10 minutes" --no-pager 2>/dev/null | grep -Eq "critical medium error, dev ${TARGET##*/}|I/O error, dev ${TARGET##*/}"; then
      fail "medium_error_persistent" "La mémoire flash présente toujours une erreur matérielle. Carte non fiable à remplacer."
    fi
    fail "filesystem_repair_failed" "La récupération du partitionnement ou du système de fichiers a échoué."
  fi

  sync
  write_report "success" "recovered" "Partitionnement et systèmes de fichiers recréés. La carte doit encore repasser la fabrication EtR complète."
  log "Récupération terminée. Relancez ensuite PRÉPARER LA CARTE."
}

main "$@"
