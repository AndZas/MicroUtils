"""USB endpoint monitoring and a nonblocking, bounded Voicemeeter repair sequence."""
import ctypes
import logging
import queue
import threading
import time
import warnings
from config import Settings, Target

log = logging.getLogger("VoicemeeterMuteTray")
DRIVERS = {1: "mme", 3: "wdm", 4: "ks"}


class Endpoints:
    """Own on the COM-initialized worker; callbacks only enqueue state changes."""
    def __init__(self):
        from pycaw.callbacks import MMNotificationClient
        from pycaw.pycaw import AudioUtilities
        events = self.events = queue.SimpleQueue()
        output_events = self.output_events = queue.SimpleQueue()
        dirty = self.outputs_dirty = threading.Event()
        dirty.set()
        self.output_devices = []
        self.output_refresh_at = 0.0
        self.capture_retry_at = 0.0

        class Callback(MMNotificationClient):
            def on_device_state_changed(self, device_id, new_state, new_state_id):
                events.put((device_id, new_state_id))
                output_events.put((device_id, new_state_id))

            def on_device_removed(self, removed_device_id):
                events.put((removed_device_id, 4))
                output_events.put((removed_device_id, 4))

            def on_device_added(self, added_device_id):
                dirty.set()

            def on_property_value_changed(self, device_id, property_struct, *args):
                if (str(property_struct.fmtid).upper() == "{A45C254E-DF1C-4EFD-8020-67D146A850E0}"
                        and property_struct.pid == 14):
                    dirty.set()  # Friendly name changed; volume changes need no enumeration.

        self.enumerator = AudioUtilities.GetDeviceEnumerator()
        self.callback = Callback()
        self.enumerator.RegisterEndpointNotificationCallback(self.callback)

    def close(self):
        self.enumerator.UnregisterEndpointNotificationCallback(self.callback)

    def find(self, name):
        from pycaw.pycaw import AudioUtilities
        # Only active capture endpoints, never a similarly named output device.
        matches = [d.id for d in AudioUtilities.GetAllDevices(data_flow=1, device_state=1)
                   if d.FriendlyName == name]
        return matches[0] if len(matches) == 1 else None

    def capture_devices(self):
        from pycaw.pycaw import AudioUtilities
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, module="pycaw")
            return tuple((d.id, d.FriendlyName) for d in AudioUtilities.GetAllDevices(data_flow=1, device_state=1)
                         if d.FriendlyName)

    def find_configured(self, name):
        """Include offline inputs when learning an existing Banana assignment."""
        from pycaw.pycaw import AudioUtilities
        matches = [d.id for d in AudioUtilities.GetAllDevices(data_flow=1) if d.FriendlyName == name]
        return matches[0] if len(matches) == 1 else None

    def default_capture(self):
        device_id = self.enumerator.GetDefaultAudioEndpoint(1, 1).GetId()  # capture, multimedia
        return next((device for device in self.capture_devices() if device[0] == device_id), None)

    def present(self, target):
        from comtypes import COMError
        try:
            if self.enumerator.GetDevice(target.endpoint_id).GetState() == 1:
                return True
        except (OSError, COMError):
            pass
        # A different USB port may produce a new endpoint ID. Match uniquely.
        now = time.monotonic()
        if now < self.capture_retry_at:
            return False
        self.capture_retry_at = now + 2
        new_id = self.find(target.name)
        if new_id:
            target.endpoint_id = new_id
            return True
        return False

    def lost_event(self, target):
        lost = False
        while True:
            try:
                device_id, state = self.events.get_nowait()
            except queue.Empty:
                return lost
            if device_id.casefold() == target.endpoint_id.casefold() and state != 1:
                lost = True

    def output_snapshot(self, now, names=None):
        from comtypes import COMError
        from pycaw.pycaw import AudioUtilities
        from bluetooth_outputs import OutputEndpoint
        # Expensive property enumeration is cached; routine checks only read state.
        if self.outputs_dirty.is_set() and now >= self.output_refresh_at:
            self.outputs_dirty.clear()
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=UserWarning, module="pycaw")
                    devices = AudioUtilities.GetAllDevices(data_flow=0)
                self.output_devices = [(d.id.casefold(), d.FriendlyName, is_bluetooth_media(d.properties))
                                       for d in devices if d.FriendlyName]
                self.output_refresh_at = now + 5
            except Exception:
                self.outputs_dirty.set()
                self.output_refresh_at = now + 10
                raise
        snapshot = []
        for device_id, name, bluetooth in self.output_devices:
            if names is not None and name not in names:
                continue
            try:
                active = self.enumerator.GetDevice(device_id).GetState() == 1
            except (OSError, COMError):
                active = False
            snapshot.append(OutputEndpoint(device_id, name, active, bluetooth))
        events = []
        while True:
            try:
                device_id, state = self.output_events.get_nowait()
                events.append((device_id.casefold(), state))
            except queue.Empty:
                return snapshot, events

    def bluetooth_snapshot(self, now):
        devices, events = self.output_snapshot(now)
        return [device for device in devices if device.bluetooth], events


def is_bluetooth_media(properties):
    """A2DP playback only; exclude the USB radio and Bluetooth hands-free audio."""
    service = str(properties.get("{A8B865DD-2E3D-4094-AD97-E593A70C75D6} 6", "")).casefold()
    return "a2dp" in service


class Recovery:
    def __init__(self, remote, endpoints, settings, notice, changed=lambda: None):
        self.remote = remote
        self.endpoints = endpoints
        self.settings = settings
        self.notice = notice
        self.changed = changed
        self.phase = "idle"
        self.status = "Проверка микрофона"
        self.pending = False
        self.attempts = 0
        self.ready_since = None
        self.next_attempt = 0.0
        self.deadline = 0.0
        self.last_restart = -100.0
        self.last_tick = -1.0
        self.was_present = None
        self.epoch_missing = False
        self.manual_requested = False

    def say(self, status, title=None, body=None):
        if self.status != status:
            self.status = status
            log.info("Recovery: %s", status)
            self.changed()
        if title:
            self.notice(title, body)

    def learn(self):
        name, driver, _ = self.remote.device()
        endpoint_id = self.endpoints.find(name) if name and driver in DRIVERS.values() else None
        if not endpoint_id:
            self.say("Не удалось определить микрофон выбранного входа")
            return False
        self.settings.target = Target(name, driver, endpoint_id)
        self.settings.save()
        self.pending = False
        self.phase = "idle"
        self.attempts = 0
        self.was_present = True
        self.epoch_missing = False
        self.say("Микрофон подключён")
        log.info("Watching microphone: %s (%s), endpoint=%s", name, driver, endpoint_id)
        return True

    def manual(self):
        if self.settings.target:
            self.manual_requested = True
            self.pending = True
            self.attempts = 0
            self.next_attempt = 0
            self.ready_since = None
            self.say("Запрошено восстановление микрофона")

    def fail(self, now, reason):
        log.warning("Repair attempt %s failed: %s", self.attempts, reason)
        self.phase = "idle"
        self.next_attempt = now + (10 if self.attempts == 1 else 30)
        if self.attempts >= self.settings.recovery_max_attempts:
            self.say("Автовосстановление не помогло", "Проверьте микрофон",
                     "Не удалось восстановить выбранный вход. Переподключите микрофон или восстановите его вручную в VoiceMeeter.")
        else:
            self.say("Повторная попытка восстановления")

    def tick(self, connected, now=None):
        now = time.monotonic() if now is None else now
        if now - self.last_tick < 0.2:
            return
        self.last_tick = now
        if not connected:
            # Never leave an interrupted detach operation forgotten on reconnect.
            if self.phase != "idle":
                self.pending = True
                self.phase = "idle"
            return
        if self.settings.target is None:
            self.learn()
            return
        target = self.settings.target
        lost = self.endpoints.lost_event(target)
        old_id = target.endpoint_id
        present = self.endpoints.present(target)
        if target.endpoint_id != old_id:
            self.settings.save()
        if lost or not present:
            if not self.epoch_missing or lost:
                self.pending = True
                self.attempts = 0
                self.phase = "idle"
                self.next_attempt = max(now, self.last_restart + 15)
                self.say("Микрофон отключён", "Микрофон отключён",
                         "Проверьте подключение устройства. Восстановление выбранного входа выполняется, если включена автоматика.")
            self.epoch_missing = not present
            self.ready_since = None
        self.was_present = present
        if not present:
            return
        self.epoch_missing = False
        if not self.pending:
            name, _, _ = self.remote.device()
            if name == target.name:
                self.say("Микрофон подключён")
            elif not name:
                self.pending = True
                self.ready_since = None
            else:
                self.say("На выбранном входе назначен другой микрофон")
            return
        if not self.settings.auto_repair and not self.manual_requested and self.phase == "idle":
            self.say("Микрофон вернулся — восстановление отключено")
            return
        if self.ready_since is None:
            self.ready_since = now
        if self.phase == "idle":
            if self.attempts >= self.settings.recovery_max_attempts or now < self.next_attempt or now - self.ready_since < self.settings.recovery_settle_seconds:
                return
            # A deliberate change of device must not be overwritten.
            name, _, _ = self.remote.device()
            if name and name != target.name:
                self.say("На выбранном входе назначен другой микрофон")
                return
            self.attempts += 1
            self.say("Восстановление микрофона")
            if not self.remote.assign(target.driver, ""):
                self.fail(now, "Could not detach device")
                return
            self.phase = "detach"
            self.deadline = now + 1.0
        elif now >= self.deadline:
            if self.phase == "detach":
                name, _, _ = self.remote.device()
                if name:
                    self.fail(now, "Detach not confirmed")
                    return
                if not self.remote.assign(target.driver, target.name):
                    self.fail(now, "Could not bind device")
                    return
                self.phase = "bind"
                self.deadline = now + 1.0
            elif self.phase == "bind":
                name, driver, _ = self.remote.device()
                if name != target.name or driver != target.driver:
                    self.fail(now, "Binding not confirmed")
                    return
                if not self.remote.restart_engine():
                    self.fail(now, "Engine restart failed")
                    return
                self.last_restart = now
                self.phase = "verify"
                self.deadline = now + 3.0
            elif self.phase == "verify":
                name, driver, rate = self.remote.device()
                if name == target.name and driver == target.driver and rate > 0:
                    self.pending = False
                    self.manual_requested = False
                    self.phase = "idle"
                    self.say("Подключение микрофона восстановлено", "Микрофон снова подключён",
                             "Выбранный вход назначен заново, аудиодвижок VoiceMeeter перезапущен.")
                    log.info("Repair confirmed: name=%s driver=%s sample_rate=%s", name, driver, rate)
                else:
                    self.fail(now, f"Verification: name={name!r}, driver={driver}, rate={rate}")
