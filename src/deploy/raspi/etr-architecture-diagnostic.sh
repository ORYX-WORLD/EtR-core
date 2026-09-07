#!/usr/bin/env bash
set -u

fail() {
  printf 'ETR_DIAG_FAIL|%s|%s\n' "$1" "$2"
  exit 1
}

pass() {
  printf 'ETR_DIAG_OK|%s|%s\n' "$1" "$2"
}

repo=/home/oryx/EtR-core
desktop=/home/oryx/Desktop/SD-V1.1.desktop
launcher=/usr/local/bin/etr-sd-factory-launch.sh
gateway=etr-remote-gateway-7n72m5gopq-ew.a.run.app
xauth=/home/oryx/.Xauthority
runner_root=/home/oryx/actions-runner/actions-runner
runner_service=actions.runner.ORYX-WORLD-EtR-core.etr-core.service

# 1. Bureau Fabrique
[ -s "$desktop" ] || fail desktop "raccourci absent: $desktop"
grep -q '^Name=SD V1.1$' "$desktop" || fail desktop "nom attendu SD V1.1 absent"
grep -q '^Exec=sudo -n /usr/local/bin/etr-sd-factory-launch.sh$' "$desktop" || fail desktop "Exec du raccourci non conforme"
pass desktop "SD V1.1 -> launcher systeme"

# 2. Launcher
[ -x "$launcher" ] || fail launcher "launcher absent ou non executable: $launcher"
grep -q 'etr-sd-factory.service' "$launcher" || fail launcher "launcher ne cible pas etr-sd-factory.service"
pass launcher "$launcher"

# 3. Service Fabrique
systemctl cat etr-sd-factory.service >/tmp/etr-diag-factory-unit.txt 2>&1 || fail factory_service "unite etr-sd-factory.service absente"
exec_start=$(systemctl show -p ExecStart --value etr-sd-factory.service 2>/dev/null || true)
printf '%s' "$exec_start" | grep -q 'etr_sd_factory_resilient.py' || fail factory_service "ExecStart ne cible pas etr_sd_factory_resilient.py"
pass factory_service "unite installee et ExecStart conforme"

# 4. Application
[ -s "$repo/src/deploy/raspi/etr_sd_factory_resilient.py" ] || fail application "etr_sd_factory_resilient.py absent"
[ -s "$repo/src/deploy/raspi/etr_sd_factory_core.py" ] || fail application "etr_sd_factory_core.py absent"
[ -s "$repo/src/deploy/raspi/etr_sd_factory_fast.py" ] || fail application "etr_sd_factory_fast.py absent"
pass application "entree resiliente + moteur + interface presentes"

# 5. Contrat framebuffer physique. L'index fb0/fb1 varie selon qu'un
# framebuffer HDMI est cree; le pilote et la geometrie constituent l'identite.
physical_fb=""
for candidate in /sys/class/graphics/fb*; do
  [ -r "$candidate/name" ] || continue
  [ -r "$candidate/virtual_size" ] || continue
  if [ "$(cat "$candidate/name")" = "fb_ili9486" ] \
    && [ "$(cat "$candidate/virtual_size")" = "480,320" ]; then
    physical_fb=$candidate
    break
  fi
done
[ -n "$physical_fb" ] || fail framebuffer "fb_ili9486 480x320 absent"
[ "$(cat "$physical_fb/bits_per_pixel")" = "16" ] || fail framebuffer "profondeur ILI9486 inattendue"
pass framebuffer "/dev/${physical_fb##*/} fb_ili9486 480x320x16"

# 6. Deux bureaux graphiques distincts
[ -e "$xauth" ] || fail display "XAUTHORITY absent"
command -v xdpyinfo >/dev/null 2>&1 || fail display "xdpyinfo absent"
DISPLAY=:1 XAUTHORITY="$xauth" xdpyinfo >/tmp/etr-diag-display1.txt 2>&1 || fail display "DISPLAY :1 inaccessible"
grep -q 'dimensions:.*480x320' /tmp/etr-diag-display1.txt || fail display "DISPLAY :1 n'est pas 480x320"
DISPLAY=:2 XAUTHORITY="$xauth" xdpyinfo >/tmp/etr-diag-display2.txt 2>&1 || fail remote_display "DISPLAY :2 inaccessible"
grep -q 'dimensions:.*1280x720' /tmp/etr-diag-display2.txt || fail remote_display "DISPLAY :2 n'est pas 1280x720"

lxde_displays=$(
  for pid in $(pgrep -x lxsession 2>/dev/null || true); do
    [ -r "/proc/$pid/environ" ] || continue
    args=$(tr '\0' ' ' < "/proc/$pid/cmdline")
    case "$args" in
      *"lxsession -s LXDE -e LXDE"*)
        tr '\0' '\n' < "/proc/$pid/environ" | sed -n 's/^DISPLAY=//p' | head -n1
        ;;
    esac
  done | sort
)
[ "$(printf '%s\n' "$lxde_displays" | grep -cx ':1')" -eq 1 ] || fail display "nombre de sessions LXDE sur :1 incorrect: $(printf '%s' "$lxde_displays" | tr '\n' ',')"
[ "$(printf '%s\n' "$lxde_displays" | grep -cx ':2')" -eq 1 ] || fail remote_display "nombre de sessions LXDE sur :2 incorrect: $(printf '%s' "$lxde_displays" | tr '\n' ',')"
[ "$(printf '%s\n' "$lxde_displays" | grep -c '^:')" -eq 2 ] || fail display "sessions LXDE supplementaires detectees"
pass display "une session LXDE physique :1 en 480x320"
pass remote_display "une session LXDE distante :2 en 1280x720"

# 7. Services du bureau distant
for service in etr-remote-desktop.service etr-vnc.service etr-remote-screen.service; do
  systemctl is-enabled --quiet "$service" || fail remote_services "$service non active au demarrage"
  systemctl is-active --quiet "$service" || fail remote_services "$service inactif"
done
ps -eo args | grep -q '[x]11vnc -display :2' || fail vnc "x11vnc ne capture pas DISPLAY :2"
ss -H -ltn 2>/dev/null | grep -qE '(127\.0\.0\.1|\[::1\]):5901' || fail vnc "VNC local absent sur 5901"
if ss -H -ltn 2>/dev/null | grep -qE '(0\.0\.0\.0|\[::\]):5901'; then
  fail vnc "VNC expose sur toutes les interfaces"
fi
pass remote_services "desktop :2 + VNC + relais actifs"
pass vnc "VNC local capture DISPLAY :2"

# 8. Reseau / DNS
getent ahosts "$gateway" >/tmp/etr-diag-dns.txt 2>&1 || fail network "DNS impossible pour $gateway"
pass network "DNS gateway resolu"

# 9. Gateway et presence appareil
command -v curl >/dev/null 2>&1 || fail gateway "curl absent"
curl -fsS --connect-timeout 5 --max-time 12 "https://$gateway/api/health" >/tmp/etr-diag-health.json 2>&1 || fail gateway "gateway /api/health inaccessible"
python3 - <<'PY' || fail gateway "reponse gateway invalide"
import json
p='/tmp/etr-diag-health.json'
d=json.load(open(p, encoding='utf-8'))
assert d.get('ok') is True
assert d.get('service') == 'etr-remote-gateway'
PY
pass gateway "health /api/health accessible"

if python3 - <<'PY'
import json
p='/tmp/etr-diag-health.json'
d=json.load(open(p, encoding='utf-8'))
raise SystemExit(0 if int(d.get('devices') or 0) >= 1 else 1)
PY
then
  pass remote_gateway "Raspberry present sur la gateway"
else
  fail remote_gateway "gateway joignable mais aucun appareil distant connecte"
fi

# 10. Runner GitHub exact
[ -x "$runner_root/bin/Runner.Listener" ] || fail runner "Runner.Listener absent: $runner_root/bin/Runner.Listener"
systemctl is-enabled --quiet "$runner_service" || fail runner "service $runner_service non active au demarrage"
systemctl is-active --quiet "$runner_service" || fail runner "service $runner_service inactif"
pgrep -af 'Runner.Listener|runsvc.sh' >/tmp/etr-diag-runner.txt 2>&1 || fail runner "Runner.Listener/runsvc.sh absent"
pass runner "$runner_service actif dans $runner_root"

printf 'ETR_DIAG_SUCCESS|all|architecture locale et distante conforme\n'
exit 0
