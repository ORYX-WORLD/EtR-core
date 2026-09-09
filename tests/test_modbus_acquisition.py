import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from src import modbus_acquisition as modbus
from src.hardware_profile import default_profile, save_profile, empty_payload, attach_profile
from src.sensor_acquisition import atomic_write_json
from src.app import create_app


def config():
    raw = modbus.default_config()
    raw.update(enabled=True, bus_exclusive_confirmed=True, installation_id="store",
               networks=[{"id": "bus", "baud_rate": 9600, "parity": "N", "stop_bits": 1}],
               points=[{"id": "modbus:store:c:t:probe", "definition": "fixture", "network_id": "bus", "slave": 1,
                        "address": 256, "function": 3, "data_type": "int16", "gain": .1,
                        "offset": 0, "mask": 0, "active_value": 0, "name": "Sonde", "unit": "°C"}])
    return raw


class ModbusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {"ETR_MODBUS_CONFIG_FILE": str(self.root/'modbus.json'),
                                     "ETR_HARDWARE_PROFILE_FILE": str(self.root/'hardware.json'),
                                     "ETR_TELEMETRY_FILE": str(self.root/'telemetry.json')})
        env.start(); self.addCleanup(env.stop)
        profile = default_profile()
        profile['ads1263']['enabled'] = False
        profile['modbus'] = {'enabled': True, 'serial_port': '/dev/ttyUSB0'}
        self.profile = save_profile(profile)
        self.api = create_app().test_client()

    def collect(self, client):
        with patch('src.hardware_profile.Path.exists', return_value=True):
            payload = attach_profile(empty_payload(self.profile), self.profile)
        result = modbus.collect(payload, self.profile, factory=lambda *_: client, sleep=lambda _: None)
        result['updated_at'] = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        return result

    def test_config_requires_exclusive_bus_no_broadcast_no_write(self):
        for key, value in [('function', 6), ('slave', 0), ('address', -1), ('gain', float('nan')), ('data_type', 'float32')]:
            raw = config(); raw['points'][0][key] = value
            with self.assertRaises(ValueError): modbus.validate_config(raw)
        raw = config(); raw['bus_exclusive_confirmed'] = False
        with self.assertRaises(ValueError): modbus.validate_config(raw)

    def test_revision_save_reload_conflict_and_origin(self):
        raw = config()
        self.assertEqual(self.api.put('/api/v1/modbus', json=raw).status_code, 403)
        headers = {'X-ETR-Local-Write':'1'}
        saved = self.api.put('/api/v1/modbus', json=raw, headers=headers)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.api.get('/api/v1/modbus').json['config']['revision'], 1)
        self.assertEqual(self.api.put('/api/v1/modbus', json=raw, headers=headers).status_code, 409)
        self.assertEqual(self.api.put('/api/v1/modbus', json=raw, headers={**headers,'Origin':'https://foreign.test'}).status_code, 403)

    def test_signed_values_bits_shared_word_and_modbus_only_online(self):
        raw = config()
        bit = {**raw['points'][0], 'id': 'modbus:store:c:t:alarm', 'data_type':'bit','mask':256,'active_value':256}
        raw['points'].append(bit); modbus.save_config(raw)
        client = Mock(); client.read_holding_registers.return_value = Mock(registers=[65526], isError=lambda: False)
        result = self.collect(client)
        self.assertEqual([p['value'] for p in result['modbus']['points']], [-1.0,1])
        client.read_holding_registers.assert_called_once_with(256, count=1, slave=1)
        self.assertEqual(result['hardware']['modbus']['status'], 'online')
        atomic_write_json(self.root/'telemetry.json', result)
        status = self.api.get('/api/v1/status').json
        self.assertTrue(status['telemetry']['online'])
        self.assertEqual(status['health'], 'ok')
        self.assertFalse(status['telemetry']['sensors'])

    def test_timeout_discards_values_and_stops_requests_to_same_slave(self):
        raw = config(); raw['points'].append({**raw['points'][0], 'id':'modbus:store:c:t:second','address':258})
        modbus.save_config(raw)
        client = Mock(); client.read_holding_registers.side_effect = TimeoutError()
        result = self.collect(client)
        self.assertEqual(client.read_holding_registers.call_count, 1)
        self.assertTrue(all('value' not in p for p in result['modbus']['points']))
        self.assertEqual(result['hardware']['modbus']['status'], 'unavailable')
        client.close.assert_called_once()

    def test_stop_and_stale_frame_clear_values_immediately(self):
        modbus.save_config(config())
        client = Mock(); client.read_holding_registers.return_value = Mock(registers=[120], isError=lambda:False)
        result = self.collect(client)
        result['updated_at'] = '2000-01-01T00:00:00+00:00'
        atomic_write_json(self.root/'telemetry.json', result)
        self.assertFalse(self.api.get('/api/v1/status').json['telemetry']['modbus']['points'])
        result = self.collect(client); atomic_write_json(self.root/'telemetry.json', result)
        raw = modbus.read_config(); raw['enabled'] = False; modbus.save_config(raw)
        status = self.api.get('/api/v1/status').json
        self.assertFalse(status['telemetry']['modbus']['points'])
        self.assertFalse(status['telemetry']['online'])

    def test_disabled_and_malformed_configuration_never_open_port(self):
        client = Mock()
        self.collect(client); client.connect.assert_not_called()

        (self.root/'modbus.json').write_text('{broken')
        self.collect(client); client.connect.assert_not_called()

    def test_bridge_preserves_real_modbus_value_and_quality(self):
        import runpy
        modbus.save_config(config())
        client = Mock(); client.read_holding_registers.return_value = Mock(registers=[0], isError=lambda:False)
        atomic_write_json(self.root/'telemetry.json', self.collect(client))
        status = self.api.get('/api/v1/status').json
        with patch.dict(os.environ, {'FIREBASE_API_KEY':'test-only','FIREBASE_DATABASE_URL':'https://database.invalid',
                                     'ETR_DEVICE_SERIAL':'00000000TEST1234'}):
            bridge = runpy.run_path(str(Path(__file__).resolve().parents[1]/'src/firebase_bridge.py'))
        with patch.object(bridge['session'],'get') as get, patch.object(bridge['session'],'put') as put:
            get.return_value.json.return_value = status
            bridge['publish']('test-token',bridge['read_local_state']())
            published = put.call_args.kwargs['json']['telemetry']['modbus']['points'][0]
            self.assertEqual(published['value'],0)
            self.assertEqual(published['status'],'ok')
            self.assertEqual(published['definition'],'fixture')

    def test_real_pymodbus_serial_read_uses_rtu_framing(self):
        # In-process serial transport: real pymodbus encodes/decodes RTU + CRC.
        from pymodbus.message.rtu import MessageRTU
        import struct
        class Serial:
            def __init__(self): self.buffer=b''; self.requests=[]
            @property
            def in_waiting(self): return len(self.buffer)
            def write(self, request):
                self.requests.append(request)
                assert request[:6] == bytes.fromhex('010301000001')
                body = bytes.fromhex('010302fff6')
                self.buffer = body + struct.pack('>H', MessageRTU.compute_CRC(body))
                return len(request)
            def read(self, size):
                result,self.buffer=self.buffer[:size],self.buffer[size:];return result
            def close(self): pass
        serial = Serial(); modbus.save_config(config())
        with patch('serial.serial_for_url', return_value=serial):
            client = modbus.client_factory('/dev/ttyUSB0', config()['networks'][0])
            result = self.collect(client)
        self.assertEqual(result['modbus']['points'][0]['value'], -1.0)
        self.assertEqual(len(serial.requests), 1)
