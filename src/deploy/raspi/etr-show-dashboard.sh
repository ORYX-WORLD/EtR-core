#!/usr/bin/env bash
# Rouvre le tableau EtR depuis le bureau Linux sans clavier ni privilège local.
set -Eeuo pipefail

endpoint="http://127.0.0.1:8090/api/local-ui/dashboard"

for _ in $(seq 1 20); do
  if /usr/bin/curl --fail --silent --show-error \
      --request POST \
      --header 'Accept: application/json' \
      --max-time 5 \
      "$endpoint" >/dev/null; then
    exit 0
  fi
  sleep 1
done

exit 1
