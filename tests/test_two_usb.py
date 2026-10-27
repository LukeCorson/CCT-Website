"""Two USB endpoints, no invented readings, and acknowledged Core/S3 commands."""
import json
import queue
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cct.contracts import CHANNELS, DEFAULT_CONFIG, required_devices
from cct.drivers.discovery import resolve_profile
from cct.drivers.hardware import HardwareRig, _Worker
from cct.drivers.profile import profile_blockers
from cct.drivers.protocols import parse_licor
from cct.service import LabService
from cct.simulator import SimulationRig
from test_drivers import commissioned_profile


def two_usb_profile():
    profile = commissioned_profile()
    profile.update(alicat_mode='manual', licor_mode='automatic', calibrator_mode='manual')
    profile['devices']['alicat'] = {}
    profile['devices']['calibrator'] = {}
    return profile


class ManualFixture(SimulationRig):
    def __init__(self, manual_licor=False):
        super().__init__()
        self.manual_licor = manual_licor

    def snapshot(self):
        result = super().snapshot()
        manual = ['alicat', 'calibrator'] + (['licor'] if self.manual_licor else [])
        result.update(calibrator_mode='manual', manual_devices=manual)
        for role in manual:
            result['devices'][role].update(required=False, connected=False, fresh=False)
        for channel, (_, _, role) in CHANNELS.items():
            if role in manual:
                result['values'][channel] = None
                result['ages'][channel] = 1e12
        return result


class TwoUsbTests(unittest.TestCase):
    def service(self, manual_licor=False):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        service = LabService(folder.name)
        service.rig = ManualFixture(manual_licor)
        service.rig.connect()
        service.connected, service.mode = True, 'hardware'
        self.addCleanup(service.disconnect)
        return service

    def settings(self, **extra):
        return dict(test_type='breakthrough', core_controls=True, manual_calibrator_confirmed=True,
                    manual_flow_confirmed=True, **extra)

    def test_only_two_ports_are_paired_and_manual_settings_need_no_protocol(self):
        profile = two_usb_profile()
        self.assertEqual(required_devices(profile), ('core2', 'licor'))
        adapters = [dict(port=profile['devices'][role]['port'], usb_serial=profile['devices'][role]['expected_usb_serial']) for role in ('core2', 'licor')]
        resolved = resolve_profile(profile, adapters)
        self.assertEqual(resolved['blockers'], [])
        self.assertEqual(profile_blockers(resolved['profile']), [])
        self.assertEqual(resolved['devices']['alicat']['state'], 'disabled')

    def test_manual_ports_do_not_create_workers(self):
        profile = two_usb_profile()
        adapters = [dict(port=profile['devices'][role]['port'], usb_serial=profile['devices'][role]['expected_usb_serial']) for role in ('core2', 'licor')]
        rig = HardwareRig('no-such-file.json')
        rig._configured_profile = profile
        rig._load_errors = []
        with patch('cct.drivers.hardware.resolve_profile', return_value=resolve_profile(profile, adapters)), patch.object(_Worker, 'start'):
            rig.connect()
        self.assertEqual(set(rig._workers), {'core2', 'licor'})

    def test_sources_close_then_reopen_without_empty_instrument_phase(self):
        rig = HardwareRig('no-such-file.json')
        rig._profile, rig._profile_errors = two_usb_profile(), []
        rig._active = rig._handshake = True
        rig._outputs['owned'] = True
        rig._workers['core2'] = _Worker(rig, 'core2', rig._profile['devices']['core2'])
        rig._devices['core2']['connected'] = True
        group = rig.execute('gas', name='air')
        closed = rig._workers['core2'].outbound.get_nowait()
        self.assertEqual(closed[3]['name'], 'off')
        self.assertTrue(rig._workers['core2'].outbound.empty())
        rig._part_done(group, closed[1])
        opened = rig._workers['core2'].outbound.get_nowait()
        self.assertEqual(opened[3]['name'], 'air')
        rig._part_done(group, opened[1])
        self.assertNotIn(group, rig._pending)
        self.assertEqual(rig._commands('safe_state', {}), [('core2', 'safe_state', {})])

    def test_route_commands_reach_core_wire_and_wait_for_matching_ack(self):
        rig = HardwareRig('no-such-file.json')
        rig._profile, rig._profile_errors = two_usb_profile(), []
        rig._active = rig._handshake = True
        rig._outputs['owned'] = True
        worker = _Worker(rig, 'core2', rig._profile['devices']['core2'])
        rig._workers['core2'] = worker
        rig._devices['core2']['connected'] = True
        frames = []
        worker.send = frames.append
        group = rig.execute('route', name='column')
        worker.active = worker.outbound.get_nowait()
        worker._start(time.monotonic())
        wire = json.loads(frames[-1])
        self.assertEqual((wire['op'], wire['name'], wire['session']), ('route', 'column', rig._session))
        self.assertIn(group, rig._pending)
        for complete in (False, True):
            reply = dict(protocol='cct-usb', version=1, type='ack', id=wire['id'], complete=complete)
            worker._receive(json.dumps(reply).encode(), time.monotonic())
            self.assertEqual(group in rig._pending, not complete)

    def test_real_co2_records_without_flow_and_needs_external_monitor_confirmation(self):
        service = self.service()
        settings = self.settings()
        settings.pop('manual_flow_confirmed')
        with self.assertRaisesRegex(ValueError, 'external flow monitoring'):
            service.start(settings)
        self.assertFalse(service.list_runs())
        service.start(self.settings())
        service.update(3)
        row = service._row()
        self.assertIsNotNone(row['co2_ppm'])
        self.assertIsNone(row['flow_sccm'])
        self.assertEqual(row['quality']['flow_sccm'], 'manual_not_recorded')
        self.assertNotIn('h2o_mmol_mol', json.dumps(row))
        self.assertTrue(service.engine.running)

    def test_manual_licor_also_runs_core_test_without_fake_co2(self):
        service = self.service(manual_licor=True)
        service.start(self.settings())
        service.update(2)
        self.assertTrue(service.engine.running)
        self.assertIsNone(service._row()['co2_ppm'])

    def test_automatic_cycles_are_blocked_for_manual_measurements(self):
        service = self.service()
        errors = service.validate_setup(dict(test_type='breakthrough', automatic_cutoff_enabled=True))
        self.assertTrue(any('Repeating automatic cycles' in text for text in errors))

    def test_hot_column_stop_takes_ten_seconds_plus_ack_not_cooldown(self):
        service = self.service()
        service.start(self.settings())
        service.update(2)
        service.rig.temperature = 100
        service.stop()
        for _ in range(9):
            service.update(1)
        self.assertTrue(service.engine.running)
        for _ in range(3):
            service.update(1)
        self.assertFalse(service.engine.running)
        self.assertTrue(service.engine.summary['cooling_required'])
        self.assertTrue(any('still hot' in warning for warning in service.snapshot()['warnings']))

    def test_lost_s3_readback_faults_instead_of_continuing(self):
        service = self.service()
        service.start(self.settings())
        snapshot = service.rig.snapshot()
        snapshot['outputs']['s3_online'] = False
        service.engine.step(1, snapshot)
        self.assertEqual(service.engine.outcome, 'fault')
        self.assertIn('S3', service.engine.reason)

    def test_shutdown_does_not_count_ten_seconds_before_output_ack(self):
        service = self.service()
        service.start(self.settings())
        service.update(2)
        service.stop()
        service.rig.outputs['pending_count'] = 1
        for _ in range(12):
            service.update(1)
        self.assertTrue(service.engine.running)
        self.assertEqual(service.engine.stage_elapsed, 0)
        service.rig.outputs['pending_count'] = 0
        for _ in range(9):
            service.update(1)
        self.assertTrue(service.engine.running)
        for _ in range(3):
            service.update(1)
        self.assertFalse(service.engine.running)

    def test_manual_flow_heating_requires_confirmation_and_preserves_temperature_faults(self):
        service = self.service()
        config = self.settings()
        config['test_type'] = 'desorption'
        service.start(config)
        service.update(2)
        service.engine.config['manual_flow_confirmed'] = False
        with self.assertRaisesRegex(ValueError, 'external nitrogen flow'):
            service.core_action('heater', 100)
        service.engine.config['manual_flow_confirmed'] = True
        service.core_action('heater', 100)
        service.rig.inject_fault('overtemp')
        service.update(1)
        self.assertEqual(service.engine.outcome, 'fault')

    def test_h2o_is_ignored_even_when_in_instrument_xml(self):
        values = parse_licor(b'<LI840><DATA><CO2>420</CO2><H2O>12.4</H2O><CELLTEMP>51.5</CELLTEMP></DATA></LI840>')
        self.assertEqual(values, {'co2_ppm': 420, 'analyzer_temp_c': 51.5})

    def test_new_sqlite_samples_never_save_h2o_even_in_nested_raw_fields(self):
        service = self.service()
        service.start(self.settings())
        row = service._row()
        row.update(h2o_mmol_mol=12, raw={'h2o_mmol_mol': 12, 'co2_ppm': 420}, ages={'h2o_mmol_mol': 0})
        service.store.append_sample(row)
        saved = service.load_run(service.run_id)
        self.assertNotIn('h2o_mmol_mol', json.dumps(saved['samples']))


if __name__ == '__main__':
    unittest.main()
