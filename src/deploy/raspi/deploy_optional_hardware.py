#!/usr/bin/env python3
"""Targeted, reversible rollout of the hardware profile on the known EtR Pi.

Does not run the full installer, change boot overlays, or touch enrollment.
Run from a separate checkout of the exact revision tested by the workflow.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.request

REPO = Path('/home/oryx/EtR-core')
STATE = Path('/var/lib/etr-core')
PROFILE = STATE / 'hardware-profile.json'
UNIT = Path('/etc/systemd/system/etr-sensor-acquisition.service')
SERVICES = ('etr-sensor-acquisition.service', 'etr.service', 'etr-dashboard.service')
AUXILIARY = ('etr-firebase-bridge.service', 'spi-desktop.service', 'etr-remote-desktop.service', 'etr-remote-screen.service')
INSTALLATION = 'etr-0000dd7429c2'
PREVIOUS_COMMIT = '8c1bd51049c5fed8bf8995d48f651714a51a3c0e'
SERIAL_PORT = '/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG041Z8E-if00-port0'


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=45).stdout.strip()


def git(*args: str) -> str:
    return run('sudo', '-u', 'oryx', 'git', '-C', str(REPO), *args)


def http(path: str, port: int = 8080, payload: dict | None = None):
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}',
        data=json.dumps(payload).encode() if payload is not None else None,
        method='PUT' if payload is not None else 'GET',
        headers={'Content-Type': 'application/json', 'X-ETR-Local-Write': '1'})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def verify_profile(status: dict, serial_port: str = SERIAL_PORT) -> None:
    assert status['device']['installation_id'] == INSTALLATION, 'Unexpected installation'
    assert status['service_version'] == '3.4.0', 'Unexpected API revision'
    profile = status['hardware_profile']
    assert profile['ads1263']['enabled'] is False, 'AD HAT is still enabled'
    assert profile['modbus'] == {'enabled': True, 'serial_port': serial_port}
    telemetry = status['telemetry']
    assert telemetry['hardware_profile'] == profile
    assert telemetry['error'] is None, telemetry['error']
    assert telemetry['hardware']['status'] == 'disabled', 'ADC not disabled'
    assert telemetry['hardware']['modbus']['status'] == 'pending_validation', 'USB port not detected'
    assert telemetry['measurements'] == {} and telemetry['sensors'] == []
    assert telemetry['online'] is False, 'No acquisition has been qualified yet'
    assert not any(alert.get('code', '').startswith('ADC_') for alert in telemetry['alerts'])
    age = time.time() - datetime.fromisoformat(telemetry['updated_at']).timestamp()
    assert -5 <= age <= 20, 'State is stale'


def wait_ready() -> dict:
    last_error = None
    for _ in range(35):
        try:
            status = http('/api/v1/status')
            verify_profile(status)
            assert http('/healthz', 8000)['version'] == '1.3.0'
            assert http('/api/hardware', 8000)['profile'] == status['hardware_profile']
            for service in SERVICES + AUXILIARY:
                assert run('systemctl', 'is-active', service) == 'active', service
            return status
        except Exception as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError('Hardware rollout verification failed') from last_error


def write_proof(commit: str, before: str, backup: Path, status: dict) -> dict:
    account = pwd.getpwnam('oryx')
    proof = {'checked_at': datetime.now(timezone.utc).isoformat(), 'commit': commit,
        'previous_commit': before, 'repository': 'ORYX-WORLD/EtR-core', 'path': str(REPO),
        'scope': 'hardware-profile-only', 'backup': str(backup),
        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'installation_id': INSTALLATION, 'profile': status['hardware_profile'],
        'telemetry': status['telemetry'], 'services': {s:run('systemctl','is-active',s) for s in SERVICES+AUXILIARY},
        'modbus_collection_verified': False,
        'files': {name:hashlib.sha256((REPO/name).read_bytes()).hexdigest() for name in [
            'src/app.py', 'src/hardware_profile.py', 'src/sensor_acquisition.py', 'src/sensor_acquisition_runtime.py',
            'dashboard/app.py', 'dashboard/static/hardware.js', 'dashboard/static/dashboard.js',
            'dashboard/templates/index.html', 'src/deploy/raspi/etr-sensor-acquisition.service']}}
    proof_path = STATE / 'hardware-deploy-proof.json'
    proof_path.write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.chown(proof_path, account.pw_uid, account.pw_gid)
    os.chmod(proof_path, 0o600)
    return proof


def deploy(commit: str) -> dict:
    assert os.geteuid() == 0, 'Root required for systemd configuration'
    assert re.fullmatch(r'[0-9a-f]{40}', commit), 'Invalid commit'
    assert git('remote','get-url','origin') == 'https://github.com/ORYX-WORLD/EtR-core.git'
    assert not git('status','--porcelain','--untracked-files=no'), 'Running checkout has local modifications'
    before = git('rev-parse','HEAD')
    assert before in {PREVIOUS_COMMIT, commit}, 'Installed revision changed since inspection'
    assert git('rev-parse',commit) == commit
    assert http('/api/v1/status')['device']['installation_id'] == INSTALLATION
    assert stat.S_ISCHR(Path(SERIAL_PORT).stat().st_mode), 'Expected USB serial device is absent'
    dropins = list(Path('/etc/systemd/system/etr-sensor-acquisition.service.d').glob('*.conf'))
    assert not dropins, 'Review acquisition service overrides before deployment'
    account = pwd.getpwnam('oryx')
    backup = STATE / ('hardware-backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    originals = {}
    for path in (PROFILE, UNIT):
        originals[path] = path.exists()
        if path.exists():
            shutil.copy2(path, backup/path.name)
    (backup/'previous-commit.txt').write_text(before+'\n')
    try:
        run('systemctl','stop',*SERVICES)
        git('checkout','--detach',commit)
        sys.path.insert(0,str(REPO))
        from src.hardware_profile import read_profile, save_profile
        profile = read_profile()
        profile['ads1263']['enabled'] = False
        profile['modbus'] = {'enabled':True, 'serial_port':SERIAL_PORT}
        save_profile(profile)
        for path in (PROFILE, PROFILE.with_suffix('.lock')):
            os.chown(path, account.pw_uid, account.pw_gid)
            os.chmod(path,0o600)
        shutil.copy2(REPO/'src/deploy/raspi/etr-sensor-acquisition.service', UNIT)
        os.chmod(UNIT,0o644)
        run('systemctl','daemon-reload')
        for service in SERVICES:
            run('systemctl','restart',service)
        status = wait_ready()
        # Verify the actual dashboard -> API save path and file permissions.
        saved = http('/api/hardware', 8000, status['hardware_profile'])
        status = wait_ready()
        assert status['hardware_profile'] == saved['profile']
        assert git('rev-parse','HEAD') == commit
        assert not git('status','--porcelain','--untracked-files=no')
        return write_proof(commit,before,backup,status)
    except Exception:
        run('systemctl','stop',*SERVICES)
        git('checkout','--detach',before)
        for path, existed in originals.items():
            if existed:
                shutil.copy2(backup/path.name,path)
                if path == PROFILE:
                    os.chown(path, account.pw_uid, account.pw_gid)
            elif path.exists():
                path.unlink()
        run('systemctl','daemon-reload')
        for service in SERVICES:
            run('systemctl','restart',service)
        print('Hardware deployment rolled back; backup:',backup)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--commit', required=True)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--require-new-boot', action='store_true')
    args = parser.parse_args()
    if args.verify:
        assert git('rev-parse','HEAD') == args.commit, 'Installed revision mismatch'
        proof = json.loads((STATE/'hardware-deploy-proof.json').read_text())
        status = wait_ready()
        current_boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if args.require_new_boot:
            assert current_boot != proof['boot_id'], 'A new boot was not observed'
            updated = write_proof(args.commit, proof['previous_commit'], Path(proof['backup']), status)
            updated['previous_boot_id'] = proof['boot_id']
            updated['reboot_verified'] = True
            (STATE/'hardware-deploy-proof.json').write_text(json.dumps(updated, ensure_ascii=False, indent=2)+'\n')
            proof = updated
        print(json.dumps(proof, ensure_ascii=False))
    else:
        print(json.dumps(deploy(args.commit), ensure_ascii=False))
