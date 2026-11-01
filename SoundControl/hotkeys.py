"""Configurable chord matching with left/right modifiers and repeat protection."""
import keyboard
import ctypes

ALIASES = {"control": "ctrl", "right control": "right ctrl", "left control": "left ctrl",
           "esc": "esc", "escape": "esc", "return": "enter", "win": "windows"}
MODIFIERS = ("ctrl", "shift", "alt", "windows")


def key_name(name):
    name = (name or "").strip().lower()
    return ALIASES.get(name, name)


def normalize_hotkey(spec):
    if not isinstance(spec, str):
        raise ValueError("Укажите клавишу или сочетание")
    tokens = [key_name(part) for part in spec.split("+")]
    if not 1 <= len(tokens) <= 4 or not all(tokens) or len(set(tokens)) != len(tokens):
        raise ValueError("Укажите от одной до четырёх разных клавиш через +")
    for modifier in MODIFIERS:
        if modifier in tokens and any(side+modifier in tokens for side in ("left ", "right ")):
            raise ValueError("Не смешивайте общий модификатор с его правой/левой версией")
    for token in tokens:
        if len(token) == 1 and token.isascii() and token.isalnum():
            continue
        try:
            keyboard.key_to_scan_codes(token)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Неизвестная клавиша: {token}") from exc
    return "+".join(tokens)


class HotkeyBinding:
    def __init__(self, spec):
        self.tokens = normalize_hotkey(spec).split("+")
        self.pressed = {}
        self.active = False
        self.codes = {}
        for token in self.tokens:
            if any(token.endswith(modifier) for modifier in MODIFIERS):
                continue
            if len(token) == 1 and token.isascii() and token.isalnum():
                self.codes[token] = {ctypes.windll.user32.MapVirtualKeyW(ord(token.upper()), 0)}
            else:
                self.codes[token] = set(keyboard.key_to_scan_codes(token))

    def reset(self):
        self.pressed.clear()
        self.active = False

    def feed(self, event):
        name = key_name(event.name)
        side = name if any(m in name for m in MODIFIERS) else ""
        identity = (getattr(event, "scan_code", name), side)
        if event.event_type == "up":
            self.pressed.pop(identity, None)
        else:
            self.pressed[identity] = name
        names = set(self.pressed.values())
        def contains(token):
            if token in MODIFIERS:
                return any(n in (token, "left "+token, "right "+token) for n in names)
            if token.startswith("left "):
                return token in names or token[5:] in names
            return token in names or any(identity[0] in self.codes.get(token, ()) for identity in self.pressed)
        matches = all(contains(token) for token in self.tokens)
        triggered = matches and not self.active and event.event_type == "down"
        self.active = matches
        return triggered
