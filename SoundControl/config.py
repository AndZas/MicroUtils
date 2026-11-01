"""Versioned, validated per-user settings. No machine-specific public defaults."""
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import hashlib
import logging
import math
from pathlib import Path
import shutil
import threading

log = logging.getLogger("VoicemeeterMuteTray")
SCHEMA_VERSION = 3
DEFAULTS = {
    "hotkey": "right ctrl", "hotkey_enabled": False, "strip_index": 0,
    "auto_repair": False, "recovery_settle_seconds": 1.5, "recovery_max_attempts": 3,
    "output_reconnect": False, "output_bus_indices": [0, 1, 2],
    "output_device_scope": "bluetooth", "output_settle_seconds": 2.5,
    "output_cooldown_seconds": 15.0, "notifications": True,
    "notification_sound": False, "notification_sound_file": "", "notice_seconds": 8.0,
    "mute_sound": True, "sound_on_file": "", "sound_off_file": "",
    "mute_volume": 70, "notification_volume": 65,
    "ui_mode": "both", "close_to_tray": True,
    "sidebar_width": 224,
    "launch_banana": False, "hide_banana_tray": False, "banana_exe": "",
}
ENUMS = {"ui_mode": {"tray", "both", "window"}, "output_device_scope": {"bluetooth", "all"}}
BOUNDS = {"strip_index": (0, 2), "recovery_max_attempts": (1, 5),
          "recovery_settle_seconds": (.5, 15), "output_settle_seconds": (.5, 15),
          "output_cooldown_seconds": (3, 120), "notice_seconds": (0, 120), "sidebar_width": (76, 320),
          "mute_volume": (0,100), "notification_volume": (0,100)}

# Only the two previously shipped samples are replaced. User WAVs stay selected.
LEGACY_SOUND_HASHES = {'734591ab1385df1c228e184f6c60253d84f12fed87bcb7ea1cb12ccf7034cc89',
                       '163c0d0aab0311de10940e2b8a8a2dc897cb1dc5c99339e200cc1d611ada59cd'}


def migrate_sound_defaults(data):
    for key in ('sound_on_file','sound_off_file'):
        path=data.get(key)
        if not isinstance(path,str) or not path:continue
        try:
            source=Path(path)
            if source.is_file() and source.stat().st_size<1024*1024 and hashlib.sha256(source.read_bytes()).hexdigest() in LEGACY_SOUND_HASHES:
                data[key]=''
        except OSError:
            pass


@dataclass
class Target:
    name: str
    driver: str
    endpoint_id: str


def validate(key, value):
    if key not in DEFAULTS:
        raise ValueError(f"Неизвестная настройка: {key}")
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if type(value) is not bool:
            raise ValueError(f"{key}: требуется переключатель")
    elif key in ENUMS:
        if not isinstance(value, str) or value not in ENUMS[key]:
            raise ValueError(f"Недопустимое значение {key}")
    elif key in BOUNDS:
        low, high = BOUNDS[key]
        if (isinstance(value, bool) or not isinstance(value, (float, int))
                or not math.isfinite(value) or not low <= value <= high):
            raise ValueError(f"{key}: допустимо от {low} до {high}")
        if isinstance(default, int) and not isinstance(value, int):
            raise ValueError(f"{key}: требуется целое число")
    elif key == "output_bus_indices":
        if not isinstance(value, list) or any(type(i) is not int or i not in (0, 1, 2) for i in value):
            raise ValueError("Допустимы только выходы A1, A2 и A3")
        value = sorted(set(value))
    elif not isinstance(value, str):
        raise ValueError(f"{key}: требуется текст")
    if key == "hotkey":
        from hotkeys import normalize_hotkey
        value = normalize_hotkey(value)
    if key == 'sidebar_width':
        value = 76 if value <= 150 else 224
    return value


class Settings:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.target = None
        self.first_run = not self.path.exists()
        self.migrated = False
        for key, value in DEFAULTS.items():
            setattr(self, key, deepcopy(value))
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Настройки должны быть объектом JSON")
            version = data.get("schema_version", 1)
            if type(version) is not int or version < 1:
                raise ValueError("Некорректная версия настроек")
            if version > SCHEMA_VERSION:
                raise RuntimeError("Настройки созданы более новой версией программы")
            if version < SCHEMA_VERSION:
                backup = self.path.with_suffix(f".v{version}.backup.json")
                if not backup.exists():
                    shutil.copyfile(self.path, backup)
                self.migrated = True
            if version < 2:
                data.setdefault("hotkey_enabled", True)
                data.setdefault("auto_repair", True)
                data.setdefault("launch_banana", True)
                data.setdefault("hide_banana_tray", True)
                data.setdefault("output_bus_indices", [1, 2])
                data.setdefault("output_reconnect", data.get("bluetooth_restart", True))
                data.setdefault("ui_mode", "tray")
                data.setdefault("notice_seconds", 0.0)
            if version < 3:
                migrate_sound_defaults(data)
            invalid = set()
            for key in DEFAULTS:
                if key in data:
                    try:
                        setattr(self, key, validate(key, data[key]))
                    except (ValueError, TypeError):
                        log.warning("Invalid setting %s; using default", key)
                        invalid.add(key)
            if "hotkey" in invalid:
                self.hotkey_enabled = False
            target = data.get("target")
            if (isinstance(target, dict) and target.get("driver") in ("wdm", "mme", "ks")
                    and all(isinstance(target.get(k), str) and target[k] for k in ("name", "endpoint_id"))):
                self.target = Target(target["name"], target["driver"], target["endpoint_id"])
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, KeyError, OSError):
            log.exception("Could not load settings; preserving file and using defaults")
            if self.path.exists():
                backup = self.path.with_suffix(".invalid.backup.json")
                try:
                    if not backup.exists():
                        shutil.copyfile(self.path, backup)
                except OSError:
                    log.exception("Could not back up invalid settings")

    @property
    def bluetooth_restart(self):
        return self.output_reconnect

    @bluetooth_restart.setter
    def bluetooth_restart(self, value):
        self.output_reconnect = value

    def to_dict(self):
        with self.lock:
            result = {key: deepcopy(getattr(self, key)) for key in DEFAULTS}
            result.update(schema_version=SCHEMA_VERSION, target=asdict(self.target) if self.target else None)
            return result

    def update(self, **changes):
        values = {key: validate(key, value) for key, value in changes.items()}
        with self.lock:
            previous = {key: getattr(self, key) for key in values}
            for key, value in values.items():
                setattr(self, key, value)
            try:
                self.save()
            except OSError:
                for key, value in previous.items():
                    setattr(self, key, value)
                raise

    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.path)
