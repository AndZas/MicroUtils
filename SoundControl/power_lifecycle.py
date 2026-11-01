"""Sleep/resume and Explorer notifications, even when settings were never opened."""
import ctypes
from ctypes import wintypes as W
import logging
import sys
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

log = logging.getLogger('VoicemeeterMuteTray')


class PowerLifecycle(QWidget):
    def __init__(self, callback):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.callback = callback
        self.suspended = False
        self.last_resume = float('-inf')
        self.last_tick = time.time()
        self.registration = None
        self.taskbar_message = None
        self.native = sys.platform == 'win32' and QGuiApplication.platformName().split(':')[0] not in ('offscreen','minimal')
        if self.native:
            # Hidden top-level HWND receives broadcasts; a message-only HWND doesn't.
            hwnd = int(self.winId())
            self.user = ctypes.WinDLL('user32', use_last_error=True)
            self.user.RegisterWindowMessageW.argtypes = [W.LPCWSTR]
            self.user.RegisterWindowMessageW.restype = W.UINT
            self.taskbar_message = self.user.RegisterWindowMessageW('TaskbarCreated')
            try:
                self.user.RegisterSuspendResumeNotification.argtypes = [W.HANDLE,W.DWORD]
                self.user.RegisterSuspendResumeNotification.restype = W.HANDLE
                self.user.UnregisterSuspendResumeNotification.argtypes = [W.HANDLE]
                self.registration = self.user.RegisterSuspendResumeNotification(hwnd,0)
            except AttributeError:
                pass  # Older Windows still broadcasts WM_POWERBROADCAST.
        self.timer = QTimer(self)
        self.timer.setInterval(15000)
        self.timer.timeout.connect(self.check_gap)
        self.timer.start()

    def notify(self, kind, now=None):
        now = time.monotonic() if now is None else now
        self.last_tick = time.time()
        if kind == 'suspend':
            if self.suspended:
                return
            self.suspended = True
        elif kind == 'resume':
            # Windows sends AUTOMATIC followed by RESUMESUSPEND, often to several HWNDs.
            if not self.suspended and now-self.last_resume < 5:
                return
            self.suspended = False
            self.last_resume = now
        log.info('Lifecycle: %s',kind)
        try:
            self.callback(kind)
        except Exception:
            log.exception('Lifecycle handler failed: %s',kind)

    def check_gap(self):
        now = time.time()
        gap = now-self.last_tick
        self.last_tick = now
        if gap > 45:
            self.notify('resume')

    def nativeEvent(self, event_type, message):
        if self.native:
            msg = W.MSG.from_address(int(message))
            if msg.message == 0x218:  # WM_POWERBROADCAST
                if msg.wParam == 4:
                    self.notify('suspend')
                elif msg.wParam in (6,7,18):
                    self.notify('resume')
            elif self.taskbar_message and msg.message == self.taskbar_message:
                self.notify('explorer')
        return super().nativeEvent(event_type,message)

    def stop(self):
        self.timer.stop()
        if self.registration:
            self.user.UnregisterSuspendResumeNotification(self.registration)
            self.registration = None
