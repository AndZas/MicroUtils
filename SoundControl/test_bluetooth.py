import unittest
from bluetooth_outputs import BluetoothOutputs, OutputEndpoint
from mic_recovery import is_bluetooth_media


class Remote:
    def __init__(self):
        self.restarts = 0
        self.last_engine_restart = float('-inf')
        self.success = True

    def restart_engine(self):
        self.restarts += 1
        return self.success


class BluetoothTests(unittest.TestCase):
    def setUp(self):
        self.remote = Remote()
        self.monitor = BluetoothOutputs(self.remote)
        self.devices = {'headphones': False, 'speaker': False}
        self.names = {'Bluetooth headphones', 'Bluetooth speaker'}
        self.now = 0
        self.enabled = True
        self.connected = True
        self.busy = False
        self.tick()

    def tick(self, events=()):
        devices = [OutputEndpoint(key, 'Bluetooth headphones' if key == 'headphones' else 'Bluetooth speaker', state)
                   for key, state in self.devices.items()]
        self.monitor.tick(devices, events, self.names, self.enabled, self.connected, self.busy, self.now)

    def advance(self, seconds):
        for _ in range(int(seconds / .5)):
            self.now += .5
            self.tick()

    def test_off_devices_never_trigger_restart(self):
        self.advance(120)
        self.assertEqual(self.remote.restarts, 0)

    def test_one_restart_per_connection(self):
        self.devices['headphones'] = True
        self.advance(2)
        self.assertEqual(self.remote.restarts, 0)
        self.advance(120)
        self.assertEqual(self.remote.restarts, 1)
        self.devices['headphones'] = False
        self.advance(1)
        self.devices['headphones'] = True
        self.advance(5)
        self.assertEqual(self.remote.restarts, 2)

    def test_two_connections_are_coalesced(self):
        self.devices['headphones'] = True
        self.advance(1)
        self.devices['speaker'] = True
        self.advance(20)
        self.assertEqual(self.remote.restarts, 1)

    def test_disconnection_during_settling_cancels_restart(self):
        self.devices['speaker'] = True
        self.advance(1)
        self.devices['speaker'] = False
        self.advance(30)
        self.assertEqual(self.remote.restarts, 0)

    def test_disabled_feature_does_not_replay_past_connection(self):
        self.enabled = False
        self.devices['headphones'] = True
        self.advance(20)
        self.enabled = True
        self.advance(20)
        self.assertEqual(self.remote.restarts, 0)

    def test_healthy_devices_at_start_are_baseline(self):
        self.monitor = BluetoothOutputs(self.remote)
        self.devices['headphones'] = True
        self.tick()
        self.advance(30)
        self.assertEqual(self.remote.restarts, 0)

    def test_unassigned_device_does_not_trigger_restart(self):
        self.names.remove('Bluetooth headphones')
        self.devices['headphones'] = True
        self.advance(30)
        self.assertEqual(self.remote.restarts, 0)

    def test_mic_recovery_defers_output_restart(self):
        self.busy = True
        self.devices['speaker'] = True
        self.advance(5)
        self.assertEqual(self.remote.restarts, 0)
        self.remote.last_engine_restart = self.now
        self.busy = False
        self.advance(20)
        self.assertEqual(self.remote.restarts, 0)
        self.assertFalse(self.monitor.pending)

    def test_wait_for_voicemeeter(self):
        self.connected = False
        self.devices['speaker'] = True
        self.advance(5)
        self.assertEqual(self.remote.restarts, 0)
        self.connected = True
        self.advance(1)
        self.assertEqual(self.remote.restarts, 1)

    def test_restart_bounce_is_ignored(self):
        self.devices['speaker'] = True
        self.advance(4)
        self.tick([('speaker', 8), ('speaker', 1)])
        self.advance(30)
        self.assertEqual(self.remote.restarts, 1)

    def test_genuine_later_short_disconnect_is_seen(self):
        self.devices['speaker'] = True
        self.advance(30)
        self.tick([('speaker', 8), ('speaker', 1)])
        self.advance(10)
        self.assertEqual(self.remote.restarts, 2)

    def test_quick_power_cycle_after_restart_waits_for_cooldown(self):
        self.devices['speaker'] = True
        self.advance(4)
        self.devices['speaker'] = False
        self.advance(.5)
        self.devices['speaker'] = True
        self.advance(5)
        self.assertEqual(self.remote.restarts, 1)
        self.advance(20)
        self.assertEqual(self.remote.restarts, 2)

    def test_restart_failures_are_bounded(self):
        self.remote.success = False
        self.devices['speaker'] = True
        self.advance(120)
        self.assertEqual(self.remote.restarts, 2)

    def test_second_device_during_cooldown_waits_then_restarts_once(self):
        self.devices['speaker'] = True
        self.advance(4)
        self.devices['headphones'] = True
        self.advance(5)
        self.assertEqual(self.remote.restarts, 1)
        self.advance(30)
        self.assertEqual(self.remote.restarts, 2)

    def test_usb_radio_and_hands_free_are_excluded(self):
        key = '{A8B865DD-2E3D-4094-AD97-E593A70C75D6} 6'
        self.assertFalse(is_bluetooth_media({key: 'USBAudio'}))
        self.assertFalse(is_bluetooth_media({key: 'BthHFEnum'}))
        self.assertTrue(is_bluetooth_media({key: 'BthA2dpSrc'}))


if __name__ == '__main__':
    unittest.main()
