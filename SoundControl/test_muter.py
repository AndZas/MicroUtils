import ctypes
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

from voicemeeter_right_ctrl_mute import Application, PARAM, Remote
from hotkeys import HotkeyBinding


class FakeDLL:
    def __init__(self):
        self.online = False
        self.muted = False
        self.cache = False
        self.pending = None
        self.writes = 0
        self.apply = True
        self.set_rc = 0

    def VBVMR_Login(self):
        return 0 if self.online else 1

    def VBVMR_Logout(self):
        return 0

    def VBVMR_IsParametersDirty(self):
        if not self.online:
            return -2
        if self.pending is not None and self.apply:
            self.muted = self.pending
            self.pending = None
        self.cache = self.muted
        return 1

    def VBVMR_GetParameterFloat(self, param, ptr):
        assert param == PARAM
        ctypes.cast(ptr, ctypes.POINTER(ctypes.c_float))[0] = float(self.cache)
        return 0

    def VBVMR_SetParameterFloat(self, param, value):
        assert param == PARAM
        self.writes += 1
        self.pending = bool(value.value)
        return self.set_rc


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.dll = FakeDLL()
        self.remote = Remote(self.dll)

    def test_start_before_voicemeeter_and_reconnect(self):
        self.assertIsNone(self.remote.read())
        self.assertIsNone(self.remote.toggle())
        self.assertEqual(self.dll.writes, 0)
        self.dll.online = True
        self.remote.retry_at = 0
        self.assertIs(self.remote.read(), False)
        self.dll.online = False
        self.assertIsNone(self.remote.read())
        self.dll.online = True
        self.dll.muted = True
        self.remote.retry_at = 0
        self.assertIs(self.remote.read(), True)

    def test_toggle_reads_external_changes(self):
        self.dll.online = True
        self.assertIs(self.remote.toggle(), True)
        self.dll.muted = False  # User clicks mute in VoiceMeeter itself.
        self.assertIs(self.remote.toggle(), True)
        self.assertIs(self.remote.toggle(), False)

    def test_failed_write_is_not_success(self):
        self.dll.online = True
        self.dll.set_rc = -2
        self.assertIsNone(self.remote.toggle())
        self.assertTrue(self.remote.logged_in)  # Parameter errors do not create new SDK sessions.

    def test_one_login_across_disconnects_and_one_logout_on_close(self):
        self.dll.VBVMR_Login=Mock(wraps=self.dll.VBVMR_Login)
        self.dll.VBVMR_Logout=Mock(wraps=self.dll.VBVMR_Logout)
        self.dll.online=True
        self.remote.read()
        for _ in range(4):
            self.dll.online=False;self.remote.read()
            self.dll.online=True;self.remote.retry_at=0;self.remote.read()
        self.dll.VBVMR_Login.assert_called_once()
        self.dll.VBVMR_Logout.assert_not_called()
        self.remote.close();self.remote.close()
        self.dll.VBVMR_Logout.assert_called_once()

    def test_unconfirmed_write_is_not_success(self):
        self.dll.online = True
        self.dll.apply = False
        self.assertIsNone(self.remote.toggle())


class KeyboardTests(unittest.TestCase):
    def setUp(self):
        self.app = Application.__new__(Application)
        self.app.key_lock = threading.Lock()
        self.app.held = False
        self.app.enabled = True
        self.app.binding = HotkeyBinding("right ctrl")
        self.app.capture_callback = None
        self.app.state = False
        self.app.suspended = threading.Event()
        self.app.requests = queue.Queue(maxsize=1)

    def key(self, name, event_type):
        self.app.on_key(SimpleNamespace(name=name, event_type=event_type))

    def test_only_right_ctrl_once_per_press(self):
        self.key('ctrl', 'down')
        self.assertTrue(self.app.requests.empty())
        self.key('right ctrl', 'down')
        self.app.requests.get_nowait()
        self.key('right ctrl', 'down')
        self.assertTrue(self.app.requests.empty())
        self.key('right ctrl', 'up')
        self.key('right ctrl', 'down')
        self.assertFalse(self.app.requests.empty())

    def test_disabled_and_disconnected_ignore_keys(self):
        self.app.enabled = False
        self.key('right ctrl', 'down')
        self.key('right ctrl', 'up')
        self.app.enabled = True
        self.app.state = None
        self.key('right ctrl', 'down')
        self.assertTrue(self.app.requests.empty())

    def test_sleep_ignores_keys_and_clears_held_chord(self):
        self.app.suspended.set()
        self.key('right ctrl','down')
        self.assertTrue(self.app.requests.empty())
        self.app.suspended.clear()
        self.key('right ctrl','down')
        self.assertFalse(self.app.requests.empty())


if __name__ == '__main__':
    unittest.main()
