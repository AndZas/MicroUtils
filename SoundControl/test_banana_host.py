import unittest
from types import SimpleNamespace
from banana_host import BananaHost


class Backend:
    def __init__(self):
        self.process = 10
        self.launches = 0
        self.states = []
        self.foregrounds = 0
        self.fail = False
        self.visible = False

    def pid(self):
        return self.process

    def launch(self):
        self.launches += 1
        if self.fail:
            raise FileNotFoundError('Banana not installed')
        self.process = 20

    def set_tray_hidden(self, hidden):
        self.states.append(hidden)
        return True

    def foreground(self):
        self.foregrounds += 1
        self.visible = True

    def window_visible(self):
        return self.visible


class Remote:
    def __init__(self):
        self.shows = []

    def show_banana(self, visible):
        self.shows.append(visible)
        return True


class HostTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.remote = Remote()
        self.settings = SimpleNamespace(launch_banana=True, hide_banana_tray=True)
        self.host = BananaHost(self.settings, self.backend, self.remote)

    def test_attaches_existing_process_without_launch(self):
        self.host.tick(True, 0)
        self.host.tick(True, 2)
        self.assertEqual(self.backend.launches, 0)
        self.assertEqual(self.remote.shows, [False])
        self.assertTrue(all(self.backend.states))

    def test_launch_waits_for_connection_before_window_command(self):
        self.backend.process = None
        self.host.tick(False, 0)
        self.host.tick(False, 2)
        self.assertEqual(self.backend.launches, 1)
        self.assertEqual(self.remote.shows, [])
        self.host.tick(True, 4)
        self.assertEqual(self.remote.shows, [False])

    def test_show_stays_open_across_subsequent_polls(self):
        self.host.tick(True, 0)
        self.host.open()
        self.host.tick(True, 1)
        self.host.tick(True, 4)
        self.host.tick(False, 6)
        self.host.tick(True, 8)
        self.assertEqual(self.remote.shows, [False, True])
        self.assertEqual(self.backend.foregrounds, 1)

    def test_manual_launch_works_when_auto_launch_disabled(self):
        self.backend.process = None
        self.settings.launch_banana = False
        self.host.tick(False, 0)
        self.assertEqual(self.backend.launches, 0)
        self.host.open()
        self.host.tick(False, 1)
        self.host.tick(True, 3)
        self.assertEqual(self.backend.launches, 1)
        self.assertEqual(self.remote.shows, [True])

    def test_attach_only_mode_changes_no_ui(self):
        self.settings.launch_banana = False
        self.settings.hide_banana_tray = False
        self.host.tick(True, 0)
        self.assertEqual(self.backend.launches, 0)
        self.assertEqual(self.backend.states, [])
        self.assertEqual(self.remote.shows, [])

    def test_toggle_hide_returns_icon_then_hides_window_once(self):
        self.host.tick(True, 0)
        self.settings.hide_banana_tray = False
        self.host.tick(True, 2)
        self.assertFalse(self.backend.states[-1])
        self.settings.hide_banana_tray = True
        self.host.tick(True, 4)
        self.host.tick(True, 6)
        self.assertEqual(self.remote.shows, [False, False])

    def test_exit_restores_icon_without_shutdown(self):
        self.host.tick(True, 0)
        self.host.close()
        self.assertFalse(self.backend.states[-1])
        self.assertEqual(self.backend.process, 10)

    def test_explicit_engine_shutdown_is_not_restarted_forever(self):
        self.host.tick(True, 0)
        self.backend.process = None
        self.host.tick(False, 2)
        self.host.tick(False, 20)
        self.assertEqual(self.backend.launches, 0)

    def test_launch_failure_is_bounded(self):
        self.backend.process = None
        self.backend.fail = True
        for now in range(0, 70, 2):
            self.host.tick(False, now)
        self.assertEqual(self.backend.launches, 3)

    def test_toggle_launch_on_starts_missing_banana(self):
        self.backend.process = None
        self.settings.launch_banana = False
        self.host.tick(False, 0)
        self.settings.launch_banana = True
        self.host.tick(False, 2)
        self.assertEqual(self.backend.launches, 1)

    def test_process_dying_before_api_is_ready_retries_launch(self):
        self.backend.process = None
        self.host.tick(False, 0)
        self.host.tick(False, 2)
        self.backend.process = None
        self.host.tick(False, 10)
        self.assertEqual(self.backend.launches, 2)

    def test_late_startup_window_is_hidden_but_explicit_open_is_preserved(self):
        self.host.tick(True, 0)
        self.backend.visible = True
        self.host.tick(True, 2)
        self.assertEqual(self.remote.shows, [False, False])
        self.host.open()
        self.host.tick(True, 3)
        self.host.tick(True, 5)
        self.assertEqual(self.remote.shows, [False, False, True])

    def test_unsolicited_window_show_after_startup_stays_in_background(self):
        self.host.tick(True, 0)
        self.backend.visible = True
        self.host.tick(True, 30)
        self.assertEqual(self.remote.shows, [False, False])


if __name__ == '__main__':
    unittest.main()
