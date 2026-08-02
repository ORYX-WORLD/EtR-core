#!/bin/sh
set +e
node server.mjs
status=$?
echo "EtR gateway process exited with status ${status}" >&2
# Le smoke test CI utilise volontairement le domaine example. Garder le
# conteneur quelques secondes permet au trap du workflow de récupérer la cause
# de démarrage avant que Docker --rm ne supprime le conteneur.
case "${FIREBASE_DATABASE_URL:-}" in
  https://example-*) sleep 45 ;;
esac
exit "$status"
