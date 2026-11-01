from types import SimpleNamespace
import unittest
from hotkeys import HotkeyBinding, normalize_hotkey


def event(name,down=True,scan=None):
    return SimpleNamespace(name=name,event_type='down' if down else 'up',scan_code=scan if scan is not None else name)


class HotkeyTests(unittest.TestCase):
    def test_right_and_left_ctrl_are_distinct(self):
        b=HotkeyBinding('right ctrl')
        self.assertFalse(b.feed(event('ctrl',scan=29)))
        self.assertTrue(b.feed(event('right ctrl',scan=29)))
        self.assertFalse(b.feed(event('right ctrl',scan=29)))
        b.feed(event('right ctrl',False,29))
        self.assertTrue(b.feed(event('right ctrl',scan=29)))

    def test_chord_with_either_side_modifiers(self):
        b=HotkeyBinding('ctrl+shift+m')
        self.assertFalse(b.feed(event('right ctrl',scan=29)))
        self.assertFalse(b.feed(event('shift',scan=42)))
        self.assertTrue(b.feed(event('m',scan=50)))
        self.assertFalse(b.feed(event('m',scan=50)))
        b.feed(event('m',False,50))
        self.assertTrue(b.feed(event('m',scan=50)))

    def test_physical_letter_survives_keyboard_layout_change(self):
        b=HotkeyBinding('ctrl+m')
        b.feed(event('ctrl',scan=29))
        self.assertTrue(b.feed(event('ь',scan=50)))
        b.feed(event('m',False,50))
        self.assertTrue(b.feed(event('m',scan=50)))

    def test_reset_releases_latched_chord(self):
        b=HotkeyBinding('f8')
        self.assertTrue(b.feed(event('f8')))
        b.reset()
        self.assertTrue(b.feed(event('f8')))

    def test_invalid_bindings_and_aliases(self):
        self.assertEqual(normalize_hotkey(' Control + Shift + M '),'ctrl+shift+m')
        for spec in ('','ctrl++m','ctrl+ctrl','one-two-three-no-key'):
            with self.assertRaises(ValueError):normalize_hotkey(spec)


if __name__=='__main__':unittest.main()
