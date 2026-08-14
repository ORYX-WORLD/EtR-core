#!/usr/bin/env bash
# Rouvre EtR depuis le bureau Linux courant.
# - Sur l'ecran physique :1, on redemarre le kiosk tactile historique.
# - Sur le bureau distant :2, on ouvre EtR dans Chromium sur cette session,
#   sans agir sur l'ecran physique.
set -Eeuo pipefail

ETR_URL="http://127.0.0.1:8090"
endpoint="${ETR_URL}/api/local-ui/dashboard"
display="${DISPLAY:-}"

if [ "$display" = ":2" ] || [[ "$display" == :2.* ]]; then
  profile="/home/oryx/.cache/etr-remote-chromium"
  install -d -m 700 "$profile"

  # Si EtR est deja ouvert sur le bureau distant, ne cree pas une pile de fenetres.
  if pgrep -u oryx -f '[c]hromium.*etr-remote-chromium' >/dev/null 2>&1; then
    exit 0
  fi

  exec /usr/bin/chromium \
    --app="$ETR_URL" \
    --start-maximized \
    --ozone-platform=x11 \
    --noerrdialogs \
    --no-first-run \
    --lang=fr-FR \
    --disable-translate \
    --disable-features=Translate,TranslateUI \
    --disable-background-networking \
    --disable-component-update \
    --disable-breakpad \
    --disable-gpu \
    --user-data-dir="$profile"
fi

# Comportement historique de l'ecran tactile physique.
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
