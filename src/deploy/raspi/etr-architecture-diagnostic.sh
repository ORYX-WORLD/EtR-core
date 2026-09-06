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

# 1. Bureau
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

# 5. Session graphique
[ -e /home/oryx/.Xauthority ] || fail display "XAUTHORITY absent"
if command -v xdpyinfo >/dev/null 2>&1; then
  DISPLAY=:1 XAUTHORITY=/home/oryx/.Xauthority xdpyinfo >/dev/null 2>&1 || fail display "DISPLAY :1 inaccessible"
  pass display "DISPLAY :1 accessible"
else
  pgrep -af 'Xorg|Xvnc|Xtigervnc|wayfire|labwc|openbox' >/dev/null 2>&1 || fail display "aucune session graphique detectee"
  pass display "session graphique detectee; xdpyinfo indisponible"
fi

# 6. Reseau / DNS
getent ahosts "$gateway" >/tmp/etr-diag-dns.txt 2>&1 || fail network "DNS impossible pour $gateway"
pass network "DNS gateway resolu"

# 7. Gateway
if command -v curl >/dev/null 2>&1; then
  curl -fsS --max-time 12 "https://$gateway/api/health" >/tmp/etr-diag-health.json 2>&1 || fail gateway "gateway /api/health inaccessible"
else
  fail gateway "curl absent"
fi
pass gateway "health accessible"

# 8. Runner GitHub
[ -d /home/oryx/actions-runner ] || fail runner "repertoire /home/oryx/actions-runner absent"
runner_service=$(systemctl list-unit-files --type=service --no-legend 2>/dev/null | awk '/actions\.runner|github.*runner|runner.*service/ {print $1; exit}')
if [ -n "$runner_service" ]; then
  systemctl is-active --quiet "$runner_service" || fail runner "service $runner_service inactif"
fi
pgrep -af 'Runner.Listener|runsvc.sh' >/tmp/etr-diag-runner.txt 2>&1 || fail runner "Runner.Listener/runsvc.sh absent"
pass runner "processus GitHub Runner actif${runner_service:+ via $runner_service}"

printf 'ETR_DIAG_SUCCESS|all|chaine locale conforme jusqu au runner\n'
exit 0
