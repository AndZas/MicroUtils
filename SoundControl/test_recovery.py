from pathlib import Path
import tempfile
import unittest

from mic_recovery import Recovery, Settings, Target


class FakeRemote:
    def __init__(self):
        self.name = 'USB Mic'
        self.driver = 'wdm'
        self.rate = 48000
        self.actions = []
        self.fail_bind = False

    def device(self):
        return self.name, self.driver, self.rate

    def assign(self, driver, name):
        self.actions.append(('assign', driver, name))
        if name and self.fail_bind:
            return False
        self.name = name
        self.driver = driver
        return True

    def restart_engine(self):
        self.actions.append(('restart',))
        return True


class FakeEndpoints:
    online = True
    lost = False

    def find(self, name):
        return 'usb-id' if self.online and name == 'USB Mic' else None

    def present(self, target):
        return self.online

    def lost_event(self, target):
        lost, self.lost = self.lost, False
        return lost


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name) / 'settings.json')
        self.settings.auto_repair = True
        self.settings.target = Target('USB Mic', 'wdm', 'usb-id')
        self.remote = FakeRemote()
        self.endpoints = FakeEndpoints()
        self.notices = []
        self.recovery = Recovery(self.remote, self.endpoints, self.settings,
                                 lambda *args: self.notices.append(args))
        self.now = 0

    def advance(self, seconds):
        for _ in range(int(seconds / .25)):
            self.now += .25
            self.recovery.tick(True, self.now)

    def disconnect(self):
        self.endpoints.online = False
        self.advance(.5)

    def test_stable_healthy_microphone_does_not_restart(self):
        self.advance(60)
        self.assertEqual(self.remote.actions, [])
        self.assertEqual(self.notices, [])

    def test_disconnect_then_rebind_and_restart_once(self):
        self.disconnect()
        self.advance(20)
        self.assertEqual(self.remote.actions, [])
        self.assertEqual(len(self.notices), 1)
        self.endpoints.online = True
        self.advance(10)
        self.assertEqual(self.remote.actions, [
            ('assign', 'wdm', ''), ('assign', 'wdm', 'USB Mic'), ('restart',)])
        self.assertFalse(self.recovery.pending)

    def test_short_disconnect_event_is_not_missed(self):
        self.endpoints.lost = True
        self.advance(10)
        self.assertIn(('restart',), self.remote.actions)

    def test_silence_and_mute_are_not_disconnect_signals(self):
        self.remote.rate = 48000
        self.advance(30)
        self.assertEqual(self.remote.actions, [])

    def test_disabled_repair_keeps_notice_and_manual_still_works(self):
        self.settings.auto_repair = False
        self.disconnect()
        self.endpoints.online = True
        self.advance(10)
        self.assertEqual(self.remote.actions, [])
        self.assertEqual(len(self.notices), 1)
        self.recovery.manual()
        self.advance(10)
        self.assertIn(('restart',), self.remote.actions)

    def test_failed_repairs_are_bounded(self):
        self.remote.fail_bind = True
        self.endpoints.lost = True
        self.advance(120)
        self.assertEqual(self.recovery.attempts, 3)
        self.assertEqual(len([a for a in self.remote.actions if a == ('assign', 'wdm', '')]), 3)
        self.assertEqual(self.recovery.status, 'Автовосстановление не помогло')
        self.assertEqual(self.notices[-1][0], 'Проверьте микрофон')

    def test_other_selected_microphone_is_not_overwritten(self):
        self.disconnect()
        self.remote.name = 'Another Mic'
        self.endpoints.online = True
        self.advance(20)
        self.assertEqual(self.remote.actions, [])

    def test_wait_for_voicemeeter(self):
        self.endpoints.lost = True
        self.recovery.tick(False, 1)
        self.assertEqual(self.remote.actions, [])
        self.advance(10)
        self.assertIn(('restart',), self.remote.actions)

    def test_new_disconnect_during_repair_is_deferred(self):
        self.recovery.manual()
        self.advance(2)
        self.assertEqual(self.recovery.phase, 'detach')
        self.disconnect()
        self.endpoints.online = True
        self.advance(10)
        self.assertFalse(self.recovery.pending)

    def test_saved_settings_and_target_roundtrip(self):
        self.settings.auto_repair = False
        self.settings.notifications = False
        self.settings.hotkey_enabled = False
        self.settings.bluetooth_restart = False
        self.settings.launch_banana = False
        self.settings.hide_banana_tray = False
        self.settings.save()
        reloaded = Settings(self.settings.path)
        self.assertFalse(reloaded.auto_repair)
        self.assertFalse(reloaded.notifications)
        self.assertFalse(reloaded.hotkey_enabled)
        self.assertFalse(reloaded.bluetooth_restart)
        self.assertFalse(reloaded.launch_banana)
        self.assertFalse(reloaded.hide_banana_tray)
        self.assertEqual(reloaded.target, self.settings.target)


if __name__ == '__main__':
    unittest.main()
