"""Right Ctrl toggles input 1. All Voicemeeter calls run on one worker."""
import argparse
import ctypes
import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import struct
import sys
import threading
import time
import winreg

import comtypes  # Initialize the importing/main thread before starting workers.
import keyboard
from app_icons import microphone_image, connection_color
from tray_menu import TrayController
from mic_recovery import DRIVERS, Endpoints, Recovery, Settings, Target
from quiet_notice import QuietNotice
from settings_window import SettingsWindow
from bluetooth_outputs import BluetoothOutputs
from banana_host import BananaHost, WindowsBanana
from hotkeys import HotkeyBinding, normalize_hotkey
import sounds
from local_control import LocalControl
from power_lifecycle import PowerLifecycle
from ui_theme import APP_VERSION

APP_NAME = "VoicemeeterMuteTray"
PARAM = b"Strip[0].Mute"
DATA_DIR = Path(os.environ["LOCALAPPDATA"]) / APP_NAME
STARTUP_BAT = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup" / (APP_NAME + ".bat")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
log = logging.getLogger(APP_NAME)


def configure_logging():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(DATA_DIR / "app.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    sys.excepthook = lambda *args: log.critical("Unhandled exception", exc_info=args)
    threading.excepthook = lambda args: log.error("Thread failed", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    # Keep a trace even for a native Qt/COM failure, which bypasses excepthook.
    global crash_log
    crash_log = (DATA_DIR/'native-errors.log').open('a',encoding='utf-8')
    faulthandler.enable(crash_log,all_threads=True)


def find_vm_dll(preferred_folder=None):
    name = "VoicemeeterRemote64.dll" if struct.calcsize("P") == 8 else "VoicemeeterRemote.dll"
    candidates = []
    if preferred_folder:
        candidates.append(Path(preferred_folder) / name)
    if os.environ.get("VOICEMEETER_REMOTE_DLL"):
        candidates.append(Path(os.environ["VOICEMEETER_REMOTE_DLL"]))
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\VB:Voicemeeter {17359A74-1236-5467}", 0, winreg.KEY_READ | view) as key:
                uninstall = winreg.QueryValueEx(key, "UninstallString")[0]
                candidates.append(Path(uninstall.strip('"')).parent / name)
        except OSError:
            pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        candidates.append(Path(os.environ.get(env, r"C:\Program Files")) / "VB/Voicemeeter" / name)
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(f"Не найдена {name}. Установите VoiceMeeter Banana.")


class Remote:
    def __init__(self, dll=None):
        self.dll = dll
        self.logged_in = False
        self.retry_at = 0.0
        self.last_error = None
        self.last_engine_restart = float("-inf")
        self.strip_index = 0
        self.dll_folder = None
        self.login_count = 0

    @property
    def mute_param(self):
        return f"Strip[{self.strip_index}].Mute".encode()

    def load(self):
        if self.dll is not None:
            return
        path = find_vm_dll(self.dll_folder)
        dll = ctypes.WinDLL(str(path))
        for name, args in (
            ("Login", []), ("Logout", []), ("IsParametersDirty", []),
            ("GetParameterFloat", [ctypes.c_char_p, ctypes.POINTER(ctypes.c_float)]),
            ("SetParameterFloat", [ctypes.c_char_p, ctypes.c_float]),
            ("GetParameterStringW", [ctypes.c_char_p, ctypes.c_wchar_p]),
            ("SetParameterStringW", [ctypes.c_char_p, ctypes.c_wchar_p]),
        ):
            func = getattr(dll, "VBVMR_" + name)
            func.restype = ctypes.c_long
            func.argtypes = args
        self.dll = dll
        log.info("Remote DLL: %s", path)

    def close(self):
        try:
            if self.logged_in:
                self.dll.VBVMR_Logout()
                log.info('Remote API session closed')
        finally:
            self.logged_in = False

    def read(self):
        if self.last_error is not None and time.monotonic() < self.retry_at:
            return None
        try:
            if not self.logged_in:
                if time.monotonic() < self.retry_at:
                    return None
                self.retry_at = time.monotonic() + 2
                self.load()
                rc = self.dll.VBVMR_Login()
                if rc not in (0, 1):
                    raise RuntimeError(f"Login: {rc}")
                self.logged_in = True
                self.login_count += 1
                log.info('Remote API session opened (login %s)',self.login_count)
            # SDK: refresh the local parameter cache, from a single thread.
            rc = self.dll.VBVMR_IsParametersDirty()
            if rc < 0:
                raise RuntimeError(f"IsParametersDirty: {rc}")
            value = ctypes.c_float()
            rc = self.dll.VBVMR_GetParameterFloat(self.mute_param, ctypes.byref(value))
            if rc != 0:
                raise RuntimeError(f"GetParameterFloat: {rc}")
            if self.last_error is not None:
                log.info("VoiceMeeter connected")
            self.last_error = None
            return value.value >= 0.5
        except (OSError, RuntimeError) as exc:
            error = str(exc)
            if error != self.last_error:
                log.warning("Waiting for VoiceMeeter: %s", error)
                self.last_error = error
            # SDK: one Login for the entire client lifetime, including periods
            # when Banana is stopped. A lost engine is not a lost SDK session.
            self.retry_at = time.monotonic() + 2
            return None

    def toggle(self):
        state = self.read()
        if state is None:
            return None
        target = not state
        rc = self.dll.VBVMR_SetParameterFloat(self.mute_param, ctypes.c_float(float(target)))
        if rc != 0:
            log.warning("SetParameterFloat: %s", rc)
            return None
        # Writes are asynchronous: only play confirmation after readback.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            time.sleep(0.02)
            actual = self.read()
            if actual is None:
                return None
            if actual == target:
                log.info("Strip[%s].Mute = %s (confirmed)", self.strip_index, int(target))
                return target
        log.warning("Mute change was not confirmed")
        return None

    def device(self):
        name = ctypes.create_unicode_buffer(512)
        rc = self.dll.VBVMR_GetParameterStringW(f"Strip[{self.strip_index}].device.name".encode(), name)
        if rc != 0:
            raise RuntimeError(f"Get device name: {rc}")
        values = []
        for param in (f"Strip[{self.strip_index}].device.type".encode(), f"Strip[{self.strip_index}].device.sr".encode()):
            value = ctypes.c_float()
            rc = self.dll.VBVMR_GetParameterFloat(param, ctypes.byref(value))
            if rc != 0:
                raise RuntimeError(f"Get {param!r}: {rc}")
            values.append(value.value)
        return name.value, DRIVERS.get(int(values[0])), values[1]

    def assign(self, driver, name):
        rc = self.dll.VBVMR_SetParameterStringW(f"Strip[{self.strip_index}].device.{driver}".encode(), name)
        log.info("Assign input %s: driver=%s name=%r rc=%s", self.strip_index+1, driver, name, rc)
        return rc == 0

    def restart_engine(self):
        rc = self.dll.VBVMR_SetParameterFloat(b"Command.Restart", ctypes.c_float(1))
        if rc == 0:
            self.last_engine_restart = time.monotonic()
        log.info("Restart audio engine: rc=%s", rc)
        return rc == 0

    def output_devices(self, buses=(0, 1, 2)):
        names = {}
        for bus in buses:
            value = ctypes.create_unicode_buffer(512)
            rc = self.dll.VBVMR_GetParameterStringW(f"Bus[{bus}].device.name".encode(), value)
            if rc != 0:
                raise RuntimeError(f"Get output A{bus+1} name: {rc}")
            if value.value:
                names[bus] = value.value
        return names

    def output_names(self, buses=(1, 2)):
        return set(self.output_devices(buses).values())

    def show_banana(self, visible):
        rc = self.dll.VBVMR_SetParameterFloat(b"Command.Show", ctypes.c_float(1 if visible else 0))
        log.info("Banana window visible=%s rc=%s", visible, rc)
        return rc == 0


def startup_command():
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    return f'"{Path(sys.executable).with_name("pythonw.exe")}" "{Path(__file__).resolve()}"'


def startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, APP_NAME)[0] == startup_command()
    except OSError:
        return False


def set_startup(enabled):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
    if STARTUP_BAT.exists():
        STARTUP_BAT.unlink()
    log.info("Startup enabled=%s command=%s", enabled, startup_command())


def tray_image(state, present=True, pending=False):
    return microphone_image(state, connection_color(state, present, pending))


class Application:
    def __init__(self, settings_path=None):
        self.remote = Remote()
        self.stop = threading.Event()
        self.settings = Settings(settings_path or DATA_DIR / "settings.json")
        self.settings.save()
        self.remote.strip_index = self.settings.strip_index
        self.remote.dll_folder = Path(self.settings.banana_exe).parent if self.settings.banana_exe else None
        self.notice = QuietNotice(self.settings, self.stop)
        self.commands = queue.SimpleQueue()
        self.recovery = None
        self.recovery_status = "Проверка микрофона"
        self.bluetooth = BluetoothOutputs(self.remote)
        self.host = None
        self.banana_status = "Подключение к Banana"
        self.requests = queue.Queue(maxsize=1)
        self.enabled = self.settings.hotkey_enabled
        self.key_lock = threading.Lock()
        self.binding = HotkeyBinding(self.settings.hotkey)
        self.capture_callback = None
        self.input_catalog = ()
        self.output_assignments = {}
        self.last_output_config = None
        self.state = None
        self.preferences = SettingsWindow(self, startup_enabled, self.toggle_startup, tray_image(False))
        self.icon = TrayController(self,tray_image(None))
        self.worker = threading.Thread(target=self.work, name="VoiceMeeter", daemon=True)
        self.control = LocalControl(APP_NAME)
        self.suspended = threading.Event()
        self.resume_pending = threading.Event()
        self.interrupted_repair = False
        self.lifecycle = PowerLifecycle(self.power_changed)

    def power_changed(self, kind):
        """Main-thread presentation recovery; the worker owns every SDK/COM call."""
        if self.stop.is_set():
            return
        if kind == 'suspend':
            sounds.reset_output()
            self.suspended.set()
            self.preferences.cancel_capture()
            self.notice.hide()
        elif kind == 'resume':
            sounds.reset_output()
            self.suspended.clear()
            self.resume_pending.set()
            self.icon.restore()
            with self.key_lock:
                self.binding.reset()
            # Reattach our callback without accumulating handlers across wakeups.
            if getattr(self,'hook',None) is not None:
                keyboard.unhook(self.hook)
                self.hook = keyboard.hook(self.on_key)
            if self.preferences.window:
                self.preferences.window.update()
        elif kind == 'explorer':
            self.icon.restore()

    def request_toggle(self, *args):
        if self.state is None:
            return  # Ignore offline presses; do not replay them after connecting.
        try:
            self.requests.put_nowait(True)
        except queue.Full:
            pass

    def on_key(self, event):
        with self.key_lock:
            if self.suspended.is_set():
                self.binding.reset()
                return
            triggered = self.binding.feed(event)
            capture = self.capture_callback
            if not capture and self.enabled and triggered:
                self.request_toggle()
        if capture:
            self.notice.call(lambda e=event, callback=capture: callback(e))

    def set_hotkey(self, spec):
        spec = normalize_hotkey(spec)
        binding = HotkeyBinding(spec)
        self.settings.update(hotkey=spec)
        with self.key_lock:
            self.binding = binding
        self.icon.update_menu()

    def begin_capture(self, callback):
        with self.key_lock:
            self.capture_callback = callback
            self.binding.reset()

    def end_capture(self):
        with self.key_lock:
            self.capture_callback = None
            self.binding.reset()

    def toggle_hotkey(self, *args):
        try:
            self.settings.update(hotkey_enabled=not self.enabled)
            self.enabled = self.settings.hotkey_enabled
        except OSError:
            log.exception("Could not save hotkey preference")

    def show_settings(self, *args):
        self.commands.put("refresh_devices")
        self.notice.call(self.preferences.show)

    def change_setting(self, key, value):
        self.settings.update(**{key: value})
        if key == "ui_mode":
            self.notice.call(self.apply_presentation)

    def apply_presentation(self):
        self.icon.visible = self.settings.ui_mode != "window"
        if self.settings.ui_mode != "tray":
            self.preferences.show()

    def open_log(self):
        os.startfile(DATA_DIR / "app.log")

    def open_banana(self, *args):
        self.commands.put("show_banana")

    def toggle_banana_launch(self, *args):
        self.settings.launch_banana = not self.settings.launch_banana
        self.settings.save()

    def toggle_banana_tray(self, *args):
        self.settings.hide_banana_tray = not self.settings.hide_banana_tray
        self.settings.save()

    def toggle_repair(self, *args):
        self.settings.auto_repair = not self.settings.auto_repair
        self.settings.save()

    def toggle_notices(self, *args):
        self.settings.notifications = not self.settings.notifications
        self.settings.save()

    def toggle_bluetooth(self, *args):
        self.settings.bluetooth_restart = not self.settings.bluetooth_restart
        self.settings.save()

    def recovery_changed(self):
        self.recovery_status = self.recovery.status
        self.update_app_icon()

    def update_app_icon(self):
        recovery = self.recovery
        self.icon.icon = tray_image(self.state, getattr(recovery, 'was_present', None),
                                   getattr(recovery, 'pending', False))
        self.notice.call(self.preferences.refresh_icon)

    def toggle_startup(self, *args):
        set_startup(not startup_enabled())

    def publish(self, state):
        if state == self.state and getattr(self, "published", False):
            return
        self.state = state
        self.published = True
        status = "Ожидание VoiceMeeter" if state is None else "Микрофон выключен" if state else "Микрофон включён"
        self.update_app_icon()
        log.info("Status: %s", status)

    def work(self):
        while not self.stop.is_set():
            try:
                self.work_once()
            except Exception:
                log.exception('Audio worker cleanup failed')
            if not self.stop.is_set():
                log.warning('Restarting audio worker after an unexpected failure')
                self.stop.wait(5)

    def release_monitor(self, endpoints):
        """Discard stale endpoint COM objects only on their owning audio thread."""
        if self.recovery:
            self.interrupted_repair |= self.recovery.phase != 'idle' or self.recovery.pending
        self.recovery = None
        self.recovery_status = 'Проверка микрофона'
        if endpoints is not None:
            try:
                endpoints.close()
            except Exception:
                log.exception('Could not unregister old endpoint monitor')
        return None

    def work_once(self):
        comtypes.CoInitializeEx(0)
        endpoints = None
        try:
            monitor_retry = 0
            host_retry = 0
            bluetooth_check_at = 0
            output_names = set()
            microphone_init_at = 0
            while not self.stop.is_set():
                if self.control.pending('quit'):
                    self.exit()
                    break
                if self.control.pending('show'):
                    self.notice.call(self.preferences.show)
                if self.suspended.is_set():
                    endpoints = self.release_monitor(endpoints)
                    self.publish(None)
                    self.stop.wait(.1)
                    continue
                if self.resume_pending.is_set():
                    self.resume_pending.clear()
                    endpoints = self.release_monitor(endpoints)
                    monitor_retry = bluetooth_check_at = microphone_init_at = 0
                    self.remote.retry_at = 0
                    # Keep the last output states: a headset connected while asleep
                    # must still count as a reconnection on the first fresh snapshot.
                    if self.host:
                        self.host.next_tick = 0
                        self.host.backend.icon = None
                    while not self.requests.empty():
                        try:
                            self.requests.get_nowait()
                        except queue.Empty:
                            break
                    log.info('Resuming audio monitoring using the existing Remote API session')
                if self.host is None and time.monotonic() >= host_retry:
                    try:
                        folder = find_vm_dll(self.remote.dll_folder).parent
                        exe = Path(self.settings.banana_exe) if self.settings.banana_exe else folder / "voicemeeterpro.exe"
                        if not exe.is_file():
                            exe = folder / "voicemeeterpro_x64.exe"
                        self.host = BananaHost(self.settings, WindowsBanana(exe), self.remote)
                        self.host.tick(False)
                    except (OSError, RuntimeError):
                        log.exception("Could not initialize Banana wrapper")
                        host_retry = time.monotonic() + 10
                if self.recovery is None and time.monotonic() >= monitor_retry:
                    try:
                        endpoints = Endpoints()
                        self.recovery = Recovery(self.remote, endpoints, self.settings,
                                                 self.notice.post, self.recovery_changed)
                        if self.interrupted_repair:
                            self.recovery.pending = True
                            self.interrupted_repair = False
                        self.input_catalog = endpoints.capture_devices()
                    except Exception:
                        log.exception("Could not initialize endpoint monitor; retrying in 10 seconds")
                        endpoints = self.release_monitor(endpoints)
                        monitor_retry = time.monotonic() + 10
                state = self.remote.read()
                self.publish(state)
                if self.control.pending('status'):
                    try:
                        device = self.remote.device() if state is not None else None
                        snapshot = {'connected':state is not None, 'muted':state, 'strip_index':self.settings.strip_index,
                                    'error':self.remote.last_error, 'device':device, 'pid':os.getpid(),
                                    'api_session_logins':self.remote.login_count,
                                    'version':APP_VERSION}
                        path = DATA_DIR/'live-status.json'
                        temp = path.with_suffix('.tmp')
                        temp.write_text(json.dumps(snapshot,ensure_ascii=False),encoding='utf-8')
                        temp.replace(path)
                        self.control.ready()
                    except (OSError,RuntimeError):
                        log.exception('Could not provide diagnostic snapshot')
                if self.settings.target is None and state is not None and endpoints is not None and time.monotonic() >= microphone_init_at:
                    self.initialize_microphone(endpoints, state)
                    microphone_init_at = time.monotonic()+5
                try:
                    command = self.commands.get_nowait()
                    if isinstance(command, dict) and command.get("kind") == "microphone":
                        success, message = self.configure_microphone(command, endpoints, state)
                        self.notice.call(lambda ok=success, msg=message: self.preferences.mic_result(ok, msg))
                    elif command == "refresh_devices":
                        try:
                            if endpoints:
                                self.input_catalog = endpoints.capture_devices()
                            self.notice.call(self.preferences.catalog_updated)
                        except Exception:
                            log.exception("Could not refresh input devices")
                            self.notice.call(self.preferences.catalog_updated)
                            self.notice.call(lambda: self.preferences.feedback('Не удалось обновить список устройств', True))
                    elif command == "learn":
                        try:
                            success = bool(state is not None and self.recovery and self.recovery.learn())
                        except (OSError, RuntimeError, comtypes.COMError):
                            log.exception("Could not learn microphone")
                            success = False
                        self.notice.call(lambda result=success: self.preferences.mic_result(result, "Текущий вход запомнен" if result else "Не удалось определить микрофон"))
                    elif command == "repair" and self.recovery:
                        self.recovery.manual()
                    elif command == "show_banana":
                        if self.host:
                            self.host.open()
                        else:
                            self.notice.post("VoiceMeeter Banana", "Не удалось найти Banana. Проверьте установку; подробности в журнале.")
                except queue.Empty:
                    pass
                if self.host:
                    try:
                        self.host.tick(state is not None)
                        self.banana_status = self.host.status
                    except Exception:
                        log.exception("Banana wrapper check failed")
                        self.host.next_tick = time.monotonic() + 10
                try:
                    if self.recovery:
                        self.recovery.tick(state is not None)
                except (OSError, RuntimeError, comtypes.COMError):
                    log.exception("Microphone recovery check failed")
                    if self.recovery:
                        self.recovery.last_tick = time.monotonic() + 4
                if endpoints is not None and time.monotonic() >= bluetooth_check_at:
                    now = time.monotonic()
                    bluetooth_check_at = now + 0.5
                    try:
                        if state is not None:
                            self.output_assignments = self.remote.output_devices()
                            output_names = {name for bus, name in self.output_assignments.items()
                                            if bus in self.settings.output_bus_indices}
                        devices, events = endpoints.output_snapshot(now, output_names)
                        if self.settings.output_device_scope == "bluetooth":
                            devices = [device for device in devices if device.bluetooth]
                        config = (tuple(self.settings.output_bus_indices), self.settings.output_device_scope)
                        if config != self.last_output_config:
                            self.bluetooth = BluetoothOutputs(self.remote)
                            self.last_output_config = config
                        self.bluetooth.tick(devices, events, output_names,
                                            self.settings.output_reconnect, state is not None,
                                            bool(self.recovery and self.recovery.phase != "idle"), now,
                                            self.settings.output_settle_seconds, self.settings.output_cooldown_seconds)
                    except Exception:
                        log.exception("Bluetooth output check failed")
                        bluetooth_check_at = now + 10
                try:
                    self.requests.get(timeout=0.02)
                except queue.Empty:
                    continue
                result = self.remote.toggle()
                self.publish(self.remote.read())
                if result is not None and self.settings.mute_sound:
                    sounds.play(self.settings.sound_off_file if result else self.settings.sound_on_file,
                                'mute' if result else 'unmute',self.settings.mute_volume)
        except Exception:
            log.exception("VoiceMeeter worker failed")
            self.publish(None)
        finally:
            try:
                if self.host:
                    self.host.close()
            finally:
                try:
                    self.remote.close()
                finally:
                    self.release_monitor(endpoints)
                    comtypes.CoUninitialize()

    def exit(self, *args):
        self.stop.set()
        self.icon.stop()

    def configure_microphone(self, command, endpoints, state):
        if state is None or endpoints is None:
            return False, "Сначала запустите VoiceMeeter Banana"
        if self.recovery and self.recovery.phase != "idle":
            return False, "Дождитесь завершения восстановления"
        index = command.get("strip")
        if type(index) is not int or index not in (0, 1, 2):
            return False, "Выберите аппаратный вход 1–3"
        old_index, old_target = self.settings.strip_index, self.settings.target
        self.publish(None)
        while True:
            try:
                self.requests.get_nowait()
            except queue.Empty:
                break
        try:
            self.remote.strip_index = index
            self.remote.read()
            selected = command.get("device")
            if not selected and command.get('fallback'):
                name, driver, _ = self.remote.device()
                if not (name and driver in DRIVERS.values() and endpoints.find(name)):
                    selected = endpoints.default_capture()
            if selected:
                device_id, name = selected
                catalog = endpoints.capture_devices()
                if (device_id, name) not in catalog:
                    raise ValueError("Устройство уже отключено. Обновите список")
                if sum(n == name for _, n in catalog) != 1:
                    raise ValueError("Устройства имеют одинаковые имена. Переименуйте одно в Windows")
                current_name, current_driver, _ = self.remote.device()
                if current_name != name or current_driver != "wdm":
                    if not self.remote.assign("wdm", name):
                        raise ValueError("VoiceMeeter отклонил назначение устройства")
                    deadline = time.monotonic()+1.5
                    while time.monotonic()<deadline:
                        time.sleep(.05)
                        if self.remote.read() is not None and self.remote.device()[0] == name:
                            break
                    else:
                        raise ValueError("Назначение не подтверждено VoiceMeeter")
                target = Target(name, "wdm", device_id)
            else:
                name, driver, _ = self.remote.device()
                device_id = endpoints.find(name) if name and driver in DRIVERS.values() else None
                if not device_id:
                    raise ValueError("На выбранном входе не найден активный микрофон Windows")
                target = Target(name, driver, device_id)
            with self.settings.lock:
                self.settings.strip_index, self.settings.target = index, target
                self.settings.save()
            self.recovery = Recovery(self.remote, endpoints, self.settings, self.notice.post, self.recovery_changed)
            self.recovery_changed()
            return True, f"Микрофон настроен: вход {index+1}"
        except (OSError, RuntimeError, ValueError, comtypes.COMError) as exc:
            with self.settings.lock:
                self.settings.strip_index, self.settings.target = old_index, old_target
            self.remote.strip_index = old_index
            log.warning("Microphone selection failed: %s", exc)
            return False, str(exc)
        finally:
            self.publish(self.remote.read())

    def initialize_microphone(self, endpoints, state):
        """Choose once. A saved choice is never replaced on startup/reconnect."""
        if self.settings.target is not None or endpoints is None or state is None:
            return False
        try:
            name, driver, _ = self.remote.device()
            device_id = endpoints.find_configured(name) if name and driver in DRIVERS.values() else None
            if device_id:
                # Learning the existing assignment preserves MME/KS and needs no
                # audio-engine restart or device write, even if it is offline.
                with self.settings.lock:
                    self.settings.target = Target(name, driver, device_id)
                    try:
                        self.settings.save()
                    except OSError:
                        self.settings.target = None
                        raise
                self.recovery = Recovery(self.remote, endpoints, self.settings, self.notice.post, self.recovery_changed)
                self.recovery_changed()
                success = True
            else:
                device = endpoints.default_capture()
                if not device:
                    return False
                success, _ = self.configure_microphone({'strip':self.settings.strip_index, 'device':device}, endpoints, state)
            if success:
                self.notice.call(self.preferences.catalog_updated)
            return success
        except (OSError, RuntimeError, ValueError, comtypes.COMError):
            log.exception('Could not choose initial microphone')
            return False

    def run(self):
        self.hook = keyboard.hook(self.on_key)
        try:
            self.icon.run_detached(setup=self.setup)
            self.notice.run()
        finally:
            self.stop.set()
            keyboard.unhook(self.hook)
            self.lifecycle.stop()
            self.icon.stop()
            if self.worker.is_alive():
                self.worker.join()
            self.control.close()
            sounds.shutdown()
            log.info("Stopped")

    def setup(self, icon):
        icon.visible = self.settings.ui_mode != "window"
        self.worker.start()
        if self.settings.ui_mode != "tray" or self.settings.first_run:
            self.notice.call(self.preferences.show)


def acquire_instance():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, "Local\\" + APP_NAME)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:
        kernel.CloseHandle(handle)
        return None
    return kernel, handle


def main():
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument('--version',action='version',version=APP_VERSION)
    parser.add_argument("--install-startup", action="store_true")
    parser.add_argument("--remove-startup", action="store_true")
    parser.add_argument("--diagnose", type=Path)
    parser.add_argument('--quit', action='store_true')
    parser.add_argument('--show-settings', action='store_true')
    parser.add_argument('--setup-pack', action='store_true')
    parser.add_argument('--preview-setup',choices=('installed','missing'))
    args = parser.parse_args()
    if args.preview_setup:
        from installer import run_setup
        run_setup(preview=True,banana_present=args.preview_setup=='installed')
        return
    if args.setup_pack or Path(sys.executable).stem.lower()=='soundcontrolsetup':
        from installer import run_setup
        run_setup()
        return
    if args.install_startup or args.remove_startup:
        set_startup(args.install_startup)
        return
    if args.quit:
        LocalControl.request(APP_NAME,'quit')
        return
    if args.show_settings and LocalControl.request(APP_NAME,'show'):
        return
    if args.diagnose:
        instance = acquire_instance()
        if instance is None:
            if not LocalControl.request(APP_NAME,'status'):
                raise RuntimeError('Не удалось получить состояние работающей утилиты')
            args.diagnose.write_text((DATA_DIR/'live-status.json').read_text(encoding='utf-8'),encoding='utf-8')
            return
        remote = Remote()
        settings = Settings(DATA_DIR / "settings.json")
        remote.strip_index = settings.strip_index
        remote.dll_folder = Path(settings.banana_exe).parent if settings.banana_exe else None
        try:
            state = remote.read()
            if state is not None:
                time.sleep(.1)
                state = remote.read()
            device = remote.device() if state is not None else None
            args.diagnose.write_text(json.dumps({"connected": state is not None, "muted": state,
                "strip_index":settings.strip_index, "error": remote.last_error, "device": device}, ensure_ascii=False), encoding="utf-8")
        finally:
            remote.close()
            instance[0].CloseHandle(instance[1])
        return
    instance = acquire_instance()
    if instance is None:
        log.info("Already running; second instance ignored")
        return
    log.info("Starting %s", sys.executable)
    try:
        app = Application()
        if args.show_settings:
            app.notice.call(app.preferences.show)
        app.run()
    finally:
        instance[0].CloseHandle(instance[1])


if __name__ == "__main__":
    main()
