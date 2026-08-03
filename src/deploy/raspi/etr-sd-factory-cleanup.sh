#!/usr/bin/env bash
set -euo pipefail

# Ne touche qu'aux points de montage créés par la fabrique EtR.
mapfile -t targets < <(/usr/bin/findmnt -rn -o TARGET | /usr/bin/awk '$0 ~ /^\/run\/etr-sd-factory\/job-[^/]+\/(root|boot)$/ {print}' | /usr/bin/sort -r)

/usr/bin/sync || true
for target in "${targets[@]:-}"; do
  [ -n "$target" ] || continue
  /usr/bin/umount "$target" 2>/dev/null || /usr/bin/umount -l "$target" 2>/dev/null || true
done

/usr/bin/find /run/etr-sd-factory -mindepth 1 -maxdepth 2 -type d -empty -delete 2>/dev/null || true
exit 0
