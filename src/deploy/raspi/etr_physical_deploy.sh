#!/usr/bin/env bash
set -euo pipefail

ETR_INSTALL_DIR=${ETR_INSTALL_DIR:-/home/oryx/EtR-core}
ETR_GATEWAY_ORIGIN=${ETR_GATEWAY_ORIGIN:-https://etr-remote-gateway-7n72m5gopq-ew.a.run.app}
FIREBASE_DATABASE_URL=${FIREBASE_DATABASE_URL:-https://oryx-froid-industriel-default-rtdb.europe-west1.firebasedatabase.app}
ETR_REPORT_PATH=${ETR_REPORT_PATH:-/tmp/etr-last-deploy.txt}

: "${GITHUB_SHA:?GITHUB_SHA is required}"
: "${ACTIONS_ID_TOKEN_REQUEST_URL:?GitHub OIDC URL is required}"
: "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:?GitHub OIDC token is required}"

target_step=initialization
bootstrap_registration=not_started
bootstrap_installation_id=
bootstrap_public_key_fingerprint=
remote_screen_connected=false
firebase_session_health=false
sensor_acquisition=false
sensor_adc_online=false
sensor_count=0
pressure_signals_valid=false
temperature_inputs_diagnosed=false
telemetry_fresh=false
telemetry_updated_at=unavailable

service_state() {
  local service=$1
  { sudo systemctl show "$service" -p ActiveState -p SubState -p Result -p User \
      --no-pager 2>/dev/null || true; } | tr '\n' ',' | sed 's/,$//'
}

http_status() {
  local url=$1
  local status
  status=$(curl -sS -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)
  printf '%s' "${status:-000}"
}

write_report() {
  local exit_code=$1
  set +e
  local installed_commit gateway_revision gateway_devices enrollment_status status
  installed_commit=$(sudo -u oryx -H git -C "$ETR_INSTALL_DIR" rev-parse HEAD 2>/dev/null \
    || echo unavailable)
  gateway_revision=$(curl -fsS "$ETR_GATEWAY_ORIGIN/api/health" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("revision","unavailable"))' \
    2>/dev/null || echo unavailable)
  gateway_devices=$(curl -fsS "$ETR_GATEWAY_ORIGIN/api/health" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("devices","unavailable"))' \
    2>/dev/null || echo unavailable)
  enrollment_status=$(curl -fsS http://127.0.0.1:8080/api/v1/enrollment 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("enrollment",{}).get("status","unavailable"))' \
    2>/dev/null || echo unavailable)
  status=failure
  [ "$exit_code" = 0 ] && status=success

  {
    echo "date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "job_status=$status"
    echo "exit_code=$exit_code"
    echo "failed_or_last_step=$target_step"
    echo "host=$(hostname)"
    echo "workflow_commit=$GITHUB_SHA"
    echo "installed_commit=$installed_commit"
    echo "commit_match=$([ "$installed_commit" = "$GITHUB_SHA" ] && echo true || echo false)"
    echo "configuration_source=firebase-hosting-reserved-url+cloud-run-fixed-origin"
    echo "gateway_origin=$ETR_GATEWAY_ORIGIN"
    echo "gateway_revision=$gateway_revision"
    echo "gateway_devices=$gateway_devices"
    echo "bootstrap_registration=$bootstrap_registration"
    echo "bootstrap_installation_id=$bootstrap_installation_id"
    echo "bootstrap_public_key_fingerprint=$bootstrap_public_key_fingerprint"
    echo "remote_screen_connected=$remote_screen_connected"
    echo "firebase_session_health=$firebase_session_health"
    echo "sensor_acquisition=$sensor_acquisition"
    echo "sensor_adc_online=$sensor_adc_online"
    echo "sensor_count=$sensor_count"
    echo "pressure_signals_valid=$pressure_signals_valid"
    echo "temperature_inputs_diagnosed=$temperature_inputs_diagnosed"
    echo "telemetry_fresh=$telemetry_fresh"
    echo "telemetry_updated_at=$telemetry_updated_at"
    for service in etr.service etr-dashboard.service etr-firebase-bridge.service \
      etr-wifi-portal.service spi-desktop.service etr-kiosk.service \
      etr-vnc.service etr-remote-screen.service etr-sensor-acquisition.service; do
      echo "$service=$(service_state "$service")"
    done
    echo "etr_api_http=$(http_status http://127.0.0.1:8080/healthz)"
    echo "etr_enrollment_http=$(http_status http://127.0.0.1:8080/api/v1/enrollment)"
    echo "etr_dashboard_http=$(http_status http://127.0.0.1:8000/healthz)"
    echo "wifi_portal_http=$(http_status http://127.0.0.1:8090/)"
    echo "enrollment_status=$enrollment_status"
  } > "$ETR_REPORT_PATH"
  cat "$ETR_REPORT_PATH"
}

on_exit() {
  local exit_code=$?
  trap - EXIT
  write_report "$exit_code"
  exit "$exit_code"
}
trap on_exit EXIT

fail_check() {
  local title=$1
  local detail=$2
  echo "::error title=$title::$detail"
  return 1
}

wait_for_service() {
  local service=$1
  local attempt
  for attempt in $(seq 1 60); do
    if sudo systemctl is-active --quiet "$service"; then return 0; fi
    sleep 2
  done
  sudo systemctl status "$service" --no-pager -l || true
  sudo journalctl -u "$service" -n 80 --no-pager || true
  fail_check "Service EtR inactif" "$service n'est pas actif"
}

target_step=verify_exact_revision
installed_commit=$(sudo -u oryx -H git -C "$ETR_INSTALL_DIR" rev-parse HEAD)
[ "$installed_commit" = "$GITHUB_SHA" ] || {
  fail_check "Révision Edge incorrecte" "$installed_commit au lieu de $GITHUB_SHA"
}
cd "$ETR_INSTALL_DIR"

target_step=configure_firebase
env_file=/etc/etr-core/firebase-bridge.env
sudo install -d -m 750 -o root -g oryx /etc/etr-core
sudo touch "$env_file"
set_value() {
  local key=$1
  local value=$2
  [ -n "$value" ] || return 0
  sudo sed -i "/^${key}=/d" "$env_file"
  printf '%s\n' "${key}=${value}" | sudo tee -a "$env_file" >/dev/null
}
config_url=https://oryx-froid-industriel.web.app/__/firebase/init.json
curl --retry 6 --retry-delay 3 --retry-all-errors -fsS "$config_url" \
  > /tmp/etr-firebase-init.json
firebase_api_key=$(python3 - <<'PY'
import json
with open('/tmp/etr-firebase-init.json', encoding='utf-8') as handle:
    data = json.load(handle)
key = str(data.get('apiKey') or '').strip()
project = str(data.get('projectId') or '').strip()
if len(key) < 20 or len(key) > 200:
    raise SystemExit('apiKey Firebase absente ou invalide')
if project != 'oryx-froid-industriel':
    raise SystemExit(f'Projet Firebase inattendu: {project}')
print(key)
PY
)
rm -f /tmp/etr-firebase-init.json
echo "::add-mask::$firebase_api_key"
gateway_origin=${ETR_GATEWAY_ORIGIN%/}
[[ "$gateway_origin" == https://*.run.app ]] || fail_check "Passerelle invalide" "$gateway_origin"
remote_gateway=${gateway_origin/https:\/\//wss:\/\/}/device
set_value FIREBASE_DATABASE_URL "$FIREBASE_DATABASE_URL"
set_value FIREBASE_API_KEY "$firebase_api_key"
set_value ETR_REMOTE_GATEWAY_WSS "$remote_gateway"
set_value FIREBASE_ENROLLMENT_URL "${gateway_origin}/api/enrollment"
unset firebase_api_key
sudo chown root:oryx "$env_file"
sudo chmod 640 "$env_file"
sudo grep -q '^FIREBASE_DATABASE_URL=.' "$env_file"
sudo grep -q '^FIREBASE_API_KEY=.' "$env_file"
sudo grep -q '^ETR_REMOTE_GATEWAY_WSS=wss://.*\.run\.app/device$' "$env_file"
sudo grep -q '^FIREBASE_ENROLLMENT_URL=https://.*\.run\.app/api/enrollment$' "$env_file"

target_step=validate_sources
python3 -m py_compile \
  "$ETR_INSTALL_DIR/src/app.py" \
  "$ETR_INSTALL_DIR/src/device_identity.py" \
  "$ETR_INSTALL_DIR/src/wifi_portal.py" \
  "$ETR_INSTALL_DIR/src/firebase_bridge.py" \
  "$ETR_INSTALL_DIR/src/remote_screen_agent.py" \
  "$ETR_INSTALL_DIR/src/ads1263.py" \
  "$ETR_INSTALL_DIR/src/sensor_acquisition.py" \
  "$ETR_INSTALL_DIR/src/sensor_acquisition_runtime.py" \
  "$ETR_INSTALL_DIR/dashboard/app.py"
test -s "$ETR_INSTALL_DIR/dashboard/requirements.txt"
test -s "$ETR_INSTALL_DIR/config/sensors-home-lab.json"
test -s "$ETR_INSTALL_DIR/src/deploy/raspi/etr-dashboard.service"
test -s "$ETR_INSTALL_DIR/src/deploy/raspi/etr-firebase-bridge.service"
test -s "$ETR_INSTALL_DIR/src/deploy/raspi/etr-remote-screen.service"
test -s "$ETR_INSTALL_DIR/src/deploy/raspi/etr-sensor-acquisition.service"
test -s "$ETR_INSTALL_DIR/src/deploy/raspi/install_sensor_acquisition.sh"

target_step=install_application
sudo -u oryx -H bash "$ETR_INSTALL_DIR/src/deploy/raspi/setup_etr.sh"
installed_commit=$(sudo -u oryx -H git -C "$ETR_INSTALL_DIR" rev-parse HEAD)
[ "$installed_commit" = "$GITHUB_SHA" ]

target_step=bootstrap_device
sudo -u oryx -H "$ETR_INSTALL_DIR/.venv/bin/python" - <<'PY'
from pathlib import Path
from src.device_identity import ensure_device_keypair
ensure_device_keypair(
    Path('/var/lib/etr-core/bootstrap-private.pem'),
    Path('/var/lib/etr-core/bootstrap-public.pem'),
)
PY
serial=$(python3 - <<'PY'
from pathlib import Path
import re
value = ''
for path in [Path('/sys/firmware/devicetree/base/serial-number'), Path('/proc/device-tree/serial-number')]:
    try:
        value = path.read_bytes().replace(b'\x00', b'').decode('ascii').strip()
        if value:
            break
    except (OSError, UnicodeDecodeError):
        pass
if not value:
    try:
        for line in Path('/proc/cpuinfo').read_text(encoding='utf-8').splitlines():
            if line.lower().startswith('serial'):
                value = line.split(':', 1)[1]
                break
    except OSError:
        pass
serial = re.sub(r'[^A-Za-z0-9]', '', value).upper()
if len(serial) < 8:
    raise SystemExit('Numéro de série Raspberry introuvable')
print(serial[-64:])
PY
)
bootstrap_installation_id="etr-${serial: -12}"
bootstrap_installation_id=$(printf '%s' "$bootstrap_installation_id" | tr '[:upper:]' '[:lower:]')
SERIAL="$serial" INSTALLATION_ID="$bootstrap_installation_id" python3 - <<'PY' \
  > /tmp/etr-bootstrap-payload.json
import json, os
from pathlib import Path
print(json.dumps({
    'serial': os.environ['SERIAL'],
    'installationId': os.environ['INSTALLATION_ID'],
    'publicKey': Path('/var/lib/etr-core/bootstrap-public.pem').read_text(encoding='ascii'),
}))
PY
oidc_url="${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=etr-bootstrap"
curl -fsS -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" "$oidc_url" \
  > /tmp/etr-github-oidc.json
oidc_token=$(python3 -c 'import json; print(json.load(open("/tmp/etr-github-oidc.json"))["value"])')
rm -f /tmp/etr-github-oidc.json
test -n "$oidc_token"
registered=false
for attempt in $(seq 1 60); do
  http_code=$(curl -sS -o /tmp/etr-bootstrap-response.json -w '%{http_code}' \
    -X POST "${gateway_origin}/api/enrollment/bootstrap" \
    -H "Authorization: Bearer ${oidc_token}" \
    -H 'Content-Type: application/json' -H 'Accept: application/json' \
    --data-binary @/tmp/etr-bootstrap-payload.json || echo 000)
  if [ "$http_code" = 201 ] || [ "$http_code" = 200 ]; then
    registered=true
    break
  fi
  if [ "$http_code" != 404 ] && [ "$http_code" != 000 ] \
    && [ "$http_code" != 502 ] && [ "$http_code" != 503 ]; then
    cat /tmp/etr-bootstrap-response.json | python3 -m json.tool \
      || cat /tmp/etr-bootstrap-response.json
    fail_check "Enregistrement EtR refusé" "HTTP $http_code"
  fi
  sleep 10
done
unset oidc_token
[ "$registered" = true ] || fail_check "Passerelle indisponible" "bootstrap non enregistré"
bootstrap_registration=success
bootstrap_public_key_fingerprint=$(python3 -c 'import json; print(json.load(open("/tmp/etr-bootstrap-response.json"))["publicKeyFingerprint"])')
rm -f /tmp/etr-bootstrap-payload.json
sudo systemctl restart etr-firebase-bridge.service

enrollment_ready=false
for attempt in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8080/api/v1/enrollment \
    > /tmp/etr-enrollment-after-bootstrap.json; then
    enrollment_state=$(python3 -c 'import json; print(json.load(open("/tmp/etr-enrollment-after-bootstrap.json"))["enrollment"]["status"])')
    if [ "$enrollment_state" = pending ] || [ "$enrollment_state" = claimed ] \
      || [ "$enrollment_state" = enrolled ]; then
      enrollment_ready=true
      break
    fi
  fi
  sleep 5
done
[ "$enrollment_ready" = true ] || {
  sudo journalctl -u etr-firebase-bridge.service -n 120 --no-pager || true
  fail_check "Enrôlement local absent" "aucun état exploitable"
}
python3 - <<'PY'
import json, re
with open('/tmp/etr-enrollment-after-bootstrap.json', encoding='utf-8') as handle:
    data = json.load(handle)['enrollment']
if data['status'] == 'pending':
    assert re.fullmatch(r'[0-9A-HJKMNP-TV-Z]{5}(?:-[0-9A-HJKMNP-TV-Z]{5}){3}', data.get('activation_code') or '')
assert 'rotationToken' not in json.dumps(data)
PY

target_step=verify_services
services=(
  etr.service
  etr-dashboard.service
  etr-wifi-portal.service
  spi-desktop.service
  etr-kiosk.service
  etr-firebase-bridge.service
  etr-vnc.service
  etr-remote-screen.service
  etr-sensor-acquisition.service
)
for service in "${services[@]}"; do wait_for_service "$service"; done
[ "$(sudo stat -c '%U:%G %a' /etc/etr-core/firebase-bridge.env)" = 'root:oryx 640' ]
[ "$(sudo stat -c '%U:%G %a' /etc/etr-core/sensors.json)" = 'root:oryx 640' ]
[ "$(sudo stat -c '%U:%G %a' /var/lib/etr-core/bootstrap-private.pem)" = 'oryx:oryx 600' ]
[ "$(sudo stat -c '%U:%G %a' /var/lib/etr-core/bootstrap-public.pem)" = 'oryx:oryx 644' ]

curl -fsS http://127.0.0.1:8080/healthz > /tmp/etr-api-health.json
curl -fsS http://127.0.0.1:8080/api/v1/status > /tmp/etr-api-status.json
curl -fsS http://127.0.0.1:8080/api/v1/enrollment > /tmp/etr-enrollment.json
curl -fsS http://127.0.0.1:8000/healthz > /tmp/etr-dashboard-health.json
curl -fsS http://127.0.0.1:8090/api/status > /tmp/etr-wifi-status.json
dashboard_html=$(curl -fsS http://127.0.0.1:8000/)
portal_html=$(curl -fsS http://127.0.0.1:8090/)
grep -Fq 'data-etr-dashboard-version' <<<"$dashboard_html"
grep -Fq 'data-enrollment' <<<"$dashboard_html"
for marker in 'Connecter cet EtR' 'Afficher la clé' '123/@'; do
grep -Fq "$marker" <<<"$portal_html"
done
python3 - <<'PY'
import json
with open('/tmp/etr-api-status.json', encoding='utf-8') as handle:
    status = json.load(handle)
with open('/tmp/etr-enrollment.json', encoding='utf-8') as handle:
    enrollment = json.load(handle)
assert status.get('schema_version') == '1.0'
assert status.get('capabilities', {}).get('secure_enrollment') is True
assert enrollment['enrollment']['status'] in {'pending', 'claimed', 'enrolled'}
assert 'rotationToken' not in json.dumps(enrollment)
PY
for port in 8000 8080; do
  sudo ss -H -ltnp | grep -qE "(127\.0\.0\.1|\[::1\]):${port}"
  ! sudo ss -H -ltnp | grep -qE "(0\.0\.0\.0|\[::\]):${port}"
done
sudo ss -H -ltnp | grep -qE '(127\.0\.0\.1|\[::1\]):5901'
! sudo ss -H -ltnp | grep -qE '(0\.0\.0\.0|\[::\]):5901'
[ "$(sudo systemctl show etr-firebase-bridge.service -p User --value)" = oryx ]
[ "$(sudo systemctl show etr-sensor-acquisition.service -p User --value)" = oryx ]

target_step=verify_sensor_acquisition
test -c /dev/spidev0.2
test -c /dev/gpiochip0
fresh=false
for attempt in $(seq 1 30); do
  if sudo test -s /var/lib/etr-core/telemetry.json; then
    age=$(($(date +%s)-$(sudo stat -c %Y /var/lib/etr-core/telemetry.json)))
    if [ "$age" -le 20 ]; then
      fresh=true
      break
    fi
  fi
  sleep 2
done
[ "$fresh" = true ] || fail_check "Télémétrie périmée" "telemetry.json n'a pas été actualisé depuis moins de 20 secondes"
telemetry_fresh=true
sudo cp /var/lib/etr-core/telemetry.json /tmp/etr-deploy-telemetry.json
sudo chown "$(id -u):$(id -g)" /tmp/etr-deploy-telemetry.json
read -r telemetry_updated_at sensor_count < <(python3 - <<'PY'
import json
from pathlib import Path

telemetry = json.loads(Path('/tmp/etr-deploy-telemetry.json').read_text(encoding='utf-8'))
api = json.loads(Path('/tmp/etr-api-status.json').read_text(encoding='utf-8'))
hardware = telemetry.get('hardware', {})
assert telemetry.get('schema_version') == '1.1', telemetry
assert hardware.get('status') == 'online', hardware
assert hardware.get('adc') == 'ADS1263', hardware
assert hardware.get('chip_id') == 1, hardware
sensors = {item.get('id'): item for item in telemetry.get('sensors', []) if isinstance(item, dict)}
assert set(sensors) == {'pressure_1', 'pressure_2', 'temperature_1', 'temperature_2'}, sensors
for identifier in ('pressure_1', 'pressure_2'):
    sample = sensors[identifier]
    assert sample.get('status') == 'ok', sample
    assert sample.get('value') is not None, sample
    assert 0.05 <= float(sample.get('signal_v')) <= 4.95, sample
allowed_ntc = {'reference_resistor_missing_or_probe_open', 'curve_required', 'ok'}
for identifier in ('temperature_1', 'temperature_2'):
    sample = sensors[identifier]
    assert sample.get('status') in allowed_ntc, sample
    assert sample.get('signal_v') is not None, sample
api_telemetry = api.get('telemetry', {})
assert api.get('capabilities', {}).get('ads1263_acquisition') is True, api.get('capabilities')
assert api_telemetry.get('hardware', {}).get('status') == 'online', api_telemetry
assert api_telemetry.get('hardware', {}).get('chip_id') == 1, api_telemetry
assert len(api_telemetry.get('sensors', [])) == 4, api_telemetry
print(str(telemetry.get('updated_at') or 'unavailable'), len(sensors))
PY
)
grep -Fq 'data-sensor-grid' <<<"$dashboard_html"
grep -Fq 'Banc d’essai capteurs' <<<"$dashboard_html"
sensor_acquisition=true
sensor_adc_online=true
pressure_signals_valid=true
temperature_inputs_diagnosed=true

target_step=verify_remote_screen
start_epoch=$(date +%s)
sudo systemctl restart etr-remote-screen.service
for attempt in $(seq 1 90); do
  journal=$(sudo journalctl -u etr-remote-screen.service --since "@${start_epoch}" \
    --no-pager 2>/dev/null || true)
  if grep -Fq 'connected to the remote gateway' <<<"$journal"; then
    remote_screen_connected=true
    break
  fi
  sleep 2
done
[ "$remote_screen_connected" = true ] || {
  sudo journalctl -u etr-remote-screen.service --since "@${start_epoch}" --no-pager || true
  fail_check "Écran distant non connecté" "aucune connexion WSS fraîche"
}
curl -fsS "$ETR_GATEWAY_ORIGIN/api/health" > /tmp/etr-gateway-health.json
python3 - <<'PY'
import json
with open('/tmp/etr-gateway-health.json', encoding='utf-8') as handle:
    data = json.load(handle)
assert data.get('ok') is True
assert int(data.get('devices', 0)) >= 1
assert data.get('revision')
PY

target_step=verify_firebase_session
oidc_url="${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=etr-bootstrap"
curl -fsS -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" "$oidc_url" \
  > /tmp/etr-edge-session-oidc.json
session_oidc=$(python3 -c 'import json; print(json.load(open("/tmp/etr-edge-session-oidc.json"))["value"])')
rm -f /tmp/etr-edge-session-oidc.json
session_status=$(curl -sS -o /tmp/etr-edge-session-health.json -w '%{http_code}' \
  -X POST "$ETR_GATEWAY_ORIGIN/api/enrollment/session-health" \
  -H "Authorization: Bearer ${session_oidc}" \
  -H 'Content-Type: application/json' -d '{}')
unset session_oidc
[ "$session_status" = 200 ] || {
  cat /tmp/etr-edge-session-health.json 2>/dev/null || true
  fail_check "Session Firebase indisponible" "HTTP $session_status"
}
python3 - <<'PY'
import json
with open('/tmp/etr-edge-session-health.json', encoding='utf-8') as handle:
    data = json.load(handle)
assert data == {'ok': True, 'mode': 'firebase-password-session', 'tokenExchange': True}
PY
firebase_session_health=true

target_step=completed
