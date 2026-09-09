"""Reversible delivery of the RTU reader, kept stopped until field configuration."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time

from deploy_optional_hardware import REPO, STATE, UNIT, SERVICES, AUXILIARY, INSTALLATION, git, http, run


def deploy(commit):
    import re
    assert os.geteuid() == 0
    assert re.fullmatch('[0-9a-f]{40}', commit)
    assert git('remote','get-url','origin') == 'https://github.com/ORYX-WORLD/EtR-core.git'
    assert not git('status','--porcelain','--untracked-files=no')
    before = git('rev-parse','HEAD')
    assert before == 'f9787431e4cd06946d7808f28866ffc9a32a4a58', 'Unexpected installed revision'
    status = http('/api/v1/status')
    assert status['device']['installation_id'] == INSTALLATION
    profile = status['hardware_profile']
    assert not profile['ads1263']['enabled']
    config_file = STATE/'modbus-config.json'
    assert not config_file.exists(), 'A field collection is already configured; review it before delivery'
    assert not list(Path('/etc/systemd/system/etr-sensor-acquisition.service.d').glob('*.conf'))
    backup = STATE/('modbus-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    shutil.copy2(UNIT, backup/UNIT.name)
    (backup/'previous-commit.txt').write_text(before+'\n')
    # Dependencies are installed and tested before stopping the running services.
    source = Path(__file__).resolve().parent
    run('bash', str(source/'install_modbus_runtime.sh'))
    try:
        run('systemctl','stop',*SERVICES)
        git('checkout','--detach',commit)
        shutil.copy2(REPO/'src/deploy/raspi/etr-sensor-acquisition.service', UNIT)
        run('systemctl','daemon-reload')
        for service in SERVICES:
            run('systemctl','restart',service)
        ready = False
        for _ in range(30):
            try:
                status = http('/api/v1/status')
                config = http('/api/v1/modbus')['config']
                telemetry = status['telemetry']
                assert status['device']['installation_id'] == INSTALLATION
                assert status['hardware_profile'] == profile
                assert status['capabilities']['modbus_acquisition'] is True
                assert config['enabled'] is False and config['points'] == []
                assert telemetry['error'] is None
                assert telemetry['modbus']['revision'] == 0
                assert telemetry['hardware']['modbus']['status'] == 'not_configured'
                assert not any(a['code'].startswith('ADC_') for a in telemetry['alerts'])
                assert time.time()-datetime.fromisoformat(telemetry['updated_at']).timestamp() < 20
                for service in SERVICES+AUXILIARY:
                    assert run('systemctl','is-active',service) == 'active'
                assert http('/healthz',8000)['ok'] is True
                ready = True
                break
            except Exception:
                time.sleep(1)
        assert ready, 'New collector failed its startup checks'
        proof = {'checked_at':datetime.now(timezone.utc).isoformat(),'commit':commit,'previous_commit':before,
                 'installation_id':INSTALLATION,'backup':str(backup),'profile':profile,'config':config,
                 'telemetry':status['telemetry'],'services':{s:run('systemctl','is-active',s) for s in SERVICES+AUXILIARY},
                 'scope':'rtu-reader-installed-stopped','physical_register_read_verified':False,
                 'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
        (STATE/'modbus-deploy-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
        os.chmod(STATE/'modbus-deploy-proof.json',0o600)
        return proof
    except Exception:
        run('systemctl','stop',*SERVICES)
        git('checkout','--detach',before)
        shutil.copy2(backup/UNIT.name,UNIT)
        run('systemctl','daemon-reload')
        for service in SERVICES:
            run('systemctl','restart',service)
        raise


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--commit',required=True)
    print(json.dumps(deploy(parser.parse_args().commit),ensure_ascii=False))
