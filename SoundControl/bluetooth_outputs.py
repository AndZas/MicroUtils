"""Reconnect selected hardware outputs. Historical module name kept for compatibility."""
from dataclasses import dataclass
import logging
import time

log = logging.getLogger("VoicemeeterMuteTray")


@dataclass(frozen=True)
class OutputEndpoint:
    id: str
    name: str
    active: bool
    bluetooth: bool = True


class BluetoothOutputs:
    SETTLE = 2.5
    COOLDOWN = 15.0

    def __init__(self, remote):
        self.remote = remote
        self.previous = None
        self.pending = {}
        self.ignored_until = {}
        self.last_attempt = float("-inf")
        self.failures = 0
        self.status = "Ожидание подключения выходов"

    def tick(self, devices, events, names, enabled, connected, mic_busy=False, now=None,
             settle_seconds=None, cooldown_seconds=None):
        now = time.monotonic() if now is None else now
        settle = self.SETTLE if settle_seconds is None else settle_seconds
        cooldown = self.COOLDOWN if cooldown_seconds is None else cooldown_seconds
        current = {d.id.casefold(): d for d in devices}
        if self.previous is None or not enabled:
            self.previous = current
            self.pending.clear()
            self.failures = 0
            self.status = "Ожидание подключения выходов" if enabled else "Перезапуск при подключении отключён"
            return
        lost = {device_id.casefold() for device_id, state in events if state != 1}
        for device_id, device in current.items():
            previous = self.previous.get(device_id)
            appeared = device.active and (previous is None or not previous.active or device_id in lost)
            observed_reconnection = previous is None or not previous.active
            if (appeared and device.name in names
                    and (observed_reconnection or now >= self.ignored_until.get(device_id, -1))):
                self.pending[device_id] = (device.name, now + settle)
                log.info("Output connected: %s; waiting for readiness", device.name)
                self.failures = 0
        self.previous = current
        self.pending = {device_id: item for device_id, item in self.pending.items()
                        if device_id in current and current[device_id].active and item[0] in names}
        if not self.pending:
            self.status = "Ожидание подключения выходов"
            return
        self.status = "Ожидание готовности устройств"
        ready_at = max(item[1] for item in self.pending.values())
        if now < ready_at or not connected or mic_busy:
            return
        # A microphone repair may already have restarted the engine for this event.
        engine_restart = getattr(self.remote, "last_engine_restart", float("-inf"))
        if engine_restart >= ready_at:
            self.finish(now, "Аудиодвижок уже перезапущен")
            return
        if now < max(self.last_attempt, engine_restart) + cooldown:
            self.status = "Ожидание перед перезапуском"
            return
        self.last_attempt = now
        names = ", ".join(item[0] for item in self.pending.values())
        if self.remote.restart_engine():
            log.info("Output engine restart: %s", names)
            self.finish(now, "Аудиодвижок перезапущен для выходов")
        else:
            self.failures += 1
            self.status = "Не удалось перезапустить аудиодвижок для выходов"
            log.warning("Output restart failed (%s/2): %s", self.failures, names)
            if self.failures >= 2:
                self.pending.clear()

    def finish(self, now, status):
        # Ignore restart-induced bounces of these same endpoints for a few seconds.
        self.ignored_until.update((device_id, now + 5) for device_id in self.pending)
        self.pending.clear()
        self.failures = 0
        self.status = status
