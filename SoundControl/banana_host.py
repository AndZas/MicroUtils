"""Launch Banana and manage its window/tray without stopping its audio engine."""
import ctypes as c
from ctypes import wintypes as w
import logging
from pathlib import Path
import subprocess
import time

import psutil

log = logging.getLogger("VoicemeeterMuteTray")
# VB-Audio's registered icon ID (confirmed in the installed executable).
# Every candidate is checked against Shell/verified Banana-owned windows.
ICON_IDS = (0x6878, *range(256))


class GUID(c.Structure):
    _fields_ = [("a", w.DWORD), ("b", w.WORD), ("d", w.WORD), ("e", w.BYTE * 8)]


class NotifyIcon(c.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hwnd", w.HWND), ("id", w.UINT),
                ("flags", w.UINT), ("callback", w.UINT), ("icon", w.HICON),
                ("tip", w.WCHAR * 128), ("state", w.DWORD), ("mask", w.DWORD),
                ("info", w.WCHAR * 256), ("version", w.UINT), ("title", w.WCHAR * 64),
                ("infoFlags", w.DWORD), ("guid", GUID), ("balloon", w.HICON)]


class IconIdentifier(c.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hwnd", w.HWND), ("id", w.UINT), ("guid", GUID)]


class WindowsBanana:
    def __init__(self, exe):
        self.exe = Path(exe).resolve()
        self.user = c.WinDLL("user32", use_last_error=True)
        self.shell = c.WinDLL("shell32", use_last_error=True)
        self.enum_callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.user.EnumWindows.argtypes = [self.enum_callback, w.LPARAM]
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        self.user.IsWindow.argtypes = [w.HWND]
        self.user.IsWindowVisible.argtypes = [w.HWND]
        self.user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
        self.user.ShowWindowAsync.argtypes = [w.HWND, c.c_int]
        self.user.SetForegroundWindow.argtypes = [w.HWND]
        self.shell.Shell_NotifyIconW.argtypes = [w.DWORD, c.POINTER(NotifyIcon)]
        self.shell.Shell_NotifyIconW.restype = w.BOOL
        self.shell.Shell_NotifyIconGetRect.argtypes = [c.POINTER(IconIdentifier), c.POINTER(w.RECT)]
        self.shell.Shell_NotifyIconGetRect.restype = c.c_long
        self.icon = None

    def pid(self):
        for process in psutil.process_iter(["pid", "name", "exe"]):
            try:
                if (process.info['name'].lower() in ("voicemeeterpro.exe", "voicemeeterpro_x64.exe")
                        and process.info['exe']
                        and Path(process.info['exe']).resolve().parent == self.exe.parent):
                    return process.info['pid']
            except (psutil.Error, OSError, AttributeError):
                continue
        return None

    def launch(self):
        if not self.exe.is_file():
            raise FileNotFoundError(f"Не найдена VoiceMeeter Banana: {self.exe}")
        subprocess.Popen([str(self.exe), "-h"], cwd=str(self.exe.parent),
                         creationflags=subprocess.CREATE_NO_WINDOW)
        log.info("Launched Banana in background: %s -h", self.exe)

    def windows(self, pid):
        windows = []
        @self.enum_callback
        def callback(hwnd, unused):
            owner = w.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, c.byref(owner))
            if owner.value == pid:
                windows.append(hwnd)
            return True
        self.user.EnumWindows(callback, 0)
        return windows

    def set_tray_hidden(self, hidden):
        pid = self.pid()
        if not pid:
            self.icon = None
            return False
        if self.icon:
            hwnd, icon_id, old_pid = self.icon
            owner = w.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, c.byref(owner))
            if old_pid != pid or owner.value != pid or not self.user.IsWindow(hwnd):
                self.icon = None
        if not self.icon:
            windows = self.windows(pid)
            # Query only windows owned by the verified Banana process.
            for hwnd in windows:
                for icon_id in ICON_IDS:
                    ident = IconIdentifier(c.sizeof(IconIdentifier), hwnd, icon_id, GUID())
                    rect = w.RECT()
                    if self.shell.Shell_NotifyIconGetRect(c.byref(ident), c.byref(rect)) == 0:
                        self.icon = (hwnd, icon_id, pid)
                        break
                if self.icon:
                    break
            if not self.icon:
                # A previous wrapper crash can leave the registered icon hidden.
                # NIM_MODIFY succeeds only for an existing (window, ID) pair.
                for hwnd in windows:
                    for icon_id in ICON_IDS:
                        if self.modify(hwnd, icon_id, hidden):
                            self.icon = (hwnd, icon_id, pid)
                            log.info("Recovered Banana tray icon: hwnd=%s id=%s", hwnd, icon_id)
                            return True
                return False
            log.info("Found Banana tray icon: hwnd=%s id=%s", *self.icon[:2])
        return self.modify(self.icon[0], self.icon[1], hidden)

    def modify(self, hwnd, icon_id, hidden):
        data = NotifyIcon()
        data.cbSize = c.sizeof(data)
        data.hwnd = hwnd
        data.id = icon_id
        data.flags = 0x8  # NIF_STATE
        data.state = int(hidden)  # NIS_HIDDEN
        data.mask = 1
        return bool(self.shell.Shell_NotifyIconW(1, c.byref(data)))  # NIM_MODIFY

    def main_window(self):
        pid = self.pid()
        if pid:
            for hwnd in self.windows(pid):
                title = c.create_unicode_buffer(256)
                self.user.GetWindowTextW(hwnd, title, len(title))
                if title.value == "Voicemeeter Banana":
                    return hwnd
        return None

    def window_visible(self):
        hwnd = self.main_window()
        return bool(hwnd and self.user.IsWindowVisible(hwnd))

    def foreground(self):
        hwnd = self.main_window()
        if hwnd:
            self.user.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
            self.user.SetForegroundWindow(hwnd)


class BananaHost:
    def __init__(self, settings, backend, remote):
        self.settings = settings
        self.backend = backend
        self.remote = remote
        self.next_tick = 0
        self.launch_done = False
        self.launch_attempts = 0
        self.launch_retry = 0
        self.last_auto = settings.launch_banana
        self.last_hide = None
        self.window_hidden_pid = None
        self.window_requested = False
        self.open_pending = False
        self.status = "Подключение к Banana"
        self.tray_touched = False

    def open(self):
        self.open_pending = True
        self.launch_attempts = 0
        self.launch_retry = 0
        self.next_tick = 0

    def tick(self, connected, now=None):
        now = time.monotonic() if now is None else now
        if now < self.next_tick:
            return
        self.next_tick = now + 2
        if self.settings.launch_banana and not self.last_auto:
            self.launch_done = False
            self.launch_attempts = 0
        self.last_auto = self.settings.launch_banana
        pid = self.backend.pid()
        if pid and connected:
            self.launch_done = True
        if not pid and (self.open_pending or (self.settings.launch_banana and not self.launch_done)):
            if now >= self.launch_retry and self.launch_attempts < 3:
                self.launch_attempts += 1
                self.launch_retry = now + 10
                try:
                    self.backend.launch()
                    self.status = "Запуск VoiceMeeter Banana…"
                except OSError:
                    log.exception("Could not launch Banana")
                    self.status = "Не удалось запустить Banana — откройте журнал"
            elif self.launch_attempts >= 3:
                self.status = "Не удалось запустить Banana — откройте журнал"
            return
        if not pid:
            self.status = "Ожидание ручного запуска Banana"
            self.window_hidden_pid = None
            self.window_requested = False
            return
        hidden = self.settings.hide_banana_tray
        if hidden or self.last_hide:
            ok = self.backend.set_tray_hidden(hidden)
            if ok:
                self.tray_touched = True
            self.status = ("Banana работает в фоне" if hidden else "Собственный значок Banana включён") if ok else "Не удалось изменить значок Banana — откройте журнал"
        else:
            self.status = "Подключено к VoiceMeeter Banana"
        if connected:
            initial = self.window_hidden_pid != pid or self.last_hide is False
            if initial:
                self.window_requested = False
            if self.open_pending:
                if self.remote.show_banana(True):
                    self.backend.foreground()
                    self.open_pending = False
                    self.window_hidden_pid = pid
                    self.window_requested = True
            elif hidden:
                visible = self.backend.window_visible()
                if self.window_requested and not visible:
                    self.window_requested = False
                if not self.window_requested and (initial or visible):
                    if self.remote.show_banana(False):
                        self.window_hidden_pid = pid
        self.last_hide = hidden

    def close(self):
        if self.tray_touched:
            try:
                self.backend.set_tray_hidden(False)
                log.info("Restored Banana tray icon; audio engine left running")
            except Exception:
                log.exception("Could not restore Banana tray icon")
