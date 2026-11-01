"""Win32 non-client integration for a Qt-drawn title bar.

Windows owns moving, resizing, Snap previews and caption double-clicks. The
client area stays custom drawn; no desktop polling or imitation Snap menu.
"""
import ctypes
from ctypes import wintypes as W
import sys
import logging
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QGuiApplication

HTCLIENT, HTCAPTION, HTMAXBUTTON = 1, 2, 9
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
log = logging.getLogger('VoicemeeterMuteTray')


class MonitorInfo(ctypes.Structure):
    _fields_ = [('cbSize', W.DWORD), ('monitor', W.RECT), ('work', W.RECT), ('flags', W.DWORD)]


class MinMaxInfo(ctypes.Structure):
    _fields_ = [(name, W.POINT) for name in ('reserved','maxSize','maxPosition','minTrack','maxTrack')]


class TrackMouse(ctypes.Structure):
    _fields_ = [('size', W.DWORD), ('flags', W.DWORD), ('hwnd', W.HWND), ('hover', W.DWORD)]


class StyleChange(ctypes.Structure):
    _fields_ = [('old',W.DWORD),('new',W.DWORD)]


def resize_hit(x, y, width, height, edge=7):
    left, right, top, bottom = x < edge, x >= width-edge, y < edge, y >= height-edge
    return (HTTOPLEFT if top and left else HTTOPRIGHT if top and right else
            HTBOTTOMLEFT if bottom and left else HTBOTTOMRIGHT if bottom and right else
            HTLEFT if left else HTRIGHT if right else HTTOP if top else HTBOTTOM if bottom else HTCLIENT)


class NativeFrame:
    def __init__(self, window):
        self.window = window
        self.platform = QGuiApplication.platformName()
        self.active = sys.platform == 'win32' and self.platform.split(':')[0].lower() not in ('offscreen','minimal')
        self.ready = False
        self.corner_pending = False
        self.horizontal_restore = None
        self.max_pressed = False
        self.seen = set()
        self.last_monitor = None
        log.info('Window integration: platform=%r enabled=%s',self.platform,self.active)
        if not self.active:
            return
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.dwm = ctypes.WinDLL('dwmapi')
        pointer_int = ctypes.c_ssize_t
        self.get_style = self.user.GetWindowLongPtrW
        self.get_style.argtypes = [W.HWND, ctypes.c_int]
        self.get_style.restype = pointer_int
        self.set_style = self.user.SetWindowLongPtrW
        self.set_style.argtypes = [W.HWND, ctypes.c_int, pointer_int]
        self.set_style.restype = pointer_int
        self.user.SetWindowPos.argtypes = [W.HWND,W.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.UINT]
        self.user.GetWindowRect.argtypes = [W.HWND,ctypes.POINTER(W.RECT)]
        self.user.ScreenToClient.argtypes = [W.HWND,ctypes.POINTER(W.POINT)]
        self.user.IsZoomed.argtypes = [W.HWND]
        self.user.MonitorFromWindow.argtypes = [W.HWND,W.DWORD]
        self.user.MonitorFromWindow.restype = W.HANDLE
        self.user.GetMonitorInfoW.argtypes = [W.HANDLE,ctypes.POINTER(MonitorInfo)]
        self.user.DefWindowProcW.argtypes = [W.HWND,W.UINT,W.WPARAM,W.LPARAM]
        self.user.DefWindowProcW.restype = pointer_int
        self.user.TrackMouseEvent.argtypes = [ctypes.POINTER(TrackMouse)]
        self.user.SetCapture.argtypes = [W.HWND]
        self.user.SetCapture.restype = W.HWND
        self.dwm.DwmSetWindowAttribute.argtypes = [W.HWND,W.DWORD,ctypes.c_void_p,W.DWORD]
        self.dwm.DwmDefWindowProc.argtypes = [W.HWND,W.UINT,W.WPARAM,W.LPARAM,ctypes.POINTER(pointer_int)]

    def configure(self):
        if not self.active:
            return
        original_geometry = self.window.geometry()
        self.hwnd = int(self.window.winId())
        # Keep native resizing/system-menu/min-max capabilities, but no OS caption.
        style = self.get_style(self.hwnd,-16)
        desired = (style & ~0x80c00000) | 0x000f0000
        was_ready,self.ready = self.ready,True
        if style != desired:
            self.set_style(self.hwnd,-16,desired)
        if style != desired or not was_ready or (self.window.isVisible() and not style & 0x10000000):
            flags = 0x0037 | (0x0040 if self.window.isVisible() else 0)
            self.user.SetWindowPos(self.hwnd,None,0,0,0,0,flags)
            if not was_ready and not self.user.IsZoomed(self.hwnd):
                self.window.setGeometry(original_geometry)
        self.update_corners()
        if not was_ready:
            log.info('Native frame configured: hwnd=%s style=%#x',self.hwnd,self.get_style(self.hwnd,-16))

    def monitor(self):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not self.user.GetMonitorInfoW(self.user.MonitorFromWindow(self.hwnd,2),ctypes.byref(info)):
            # Display devices can be temporarily absent during a sleep transition.
            if self.last_monitor is not None:
                return self.last_monitor
            raise ctypes.WinError(ctypes.get_last_error())
        self.last_monitor = info
        return info

    def point(self, lparam):
        point = W.POINT(ctypes.c_short(lparam & 0xffff).value, ctypes.c_short((lparam >> 16) & 0xffff).value)
        self.user.ScreenToClient(self.hwnd,ctypes.byref(point))
        ratio = self.window.devicePixelRatioF()
        return QPoint(round(point.x/ratio),round(point.y/ratio))

    def hit_test(self, point):
        window = self.window
        if not self.user.IsZoomed(self.hwnd):
            hit = resize_hit(point.x(),point.y(),window.width(),window.height())
            if hit != HTCLIENT:
                return hit
        title = getattr(window,'title_bar',None)
        if title:
            local = title.mapFrom(window,point)
            if title.max_button.geometry().contains(local):
                return HTMAXBUTTON
            if title.rect().contains(local) and not any(b.geometry().contains(local) for b in title.buttons):
                return HTCAPTION
        return HTCLIENT

    def update_corners(self):
        self.corner_pending = False
        if not self.ready:
            return
        rect = W.RECT()
        if not self.user.GetWindowRect(self.hwnd,ctypes.byref(rect)):
            return
        work = self.monitor().work
        near = lambda a,b: abs(a-b) <= round(9*self.window.devicePixelRatioF())
        left,right = near(rect.left,work.left),near(rect.right,work.right)
        top,bottom = near(rect.top,work.top),near(rect.bottom,work.bottom)
        flat = bool(self.user.IsZoomed(self.hwnd) or (left and right) or (top and bottom) or ((left or right) and (top or bottom)))
        self.window.square_corners = flat
        preference = ctypes.c_int(1 if flat else 2)
        self.dwm.DwmSetWindowAttribute(self.hwnd,33,ctypes.byref(preference),4)
        self.window.update()

    def stretch_horizontal(self):
        current = self.window.geometry()
        work = self.window.screen().availableGeometry()
        if current.left() == work.left() and current.width() == work.width() and self.horizontal_restore:
            self.restore_horizontal()
            return
        else:
            self.horizontal_restore = current.left(),current.width()
            current.setLeft(work.left())
            current.setWidth(work.width())
        self.window.setGeometry(current)

    def restore_horizontal(self, pointer_x=None):
        if not self.horizontal_restore:
            return False
        current = self.window.geometry()
        old_x, old_width = self.horizontal_restore
        self.horizontal_restore = None
        if pointer_x is not None:
            fraction = min(1, max(0, (pointer_x-current.left())/max(1,current.width())))
            old_x = round(pointer_x-fraction*old_width)
        current.setLeft(old_x)
        current.setWidth(old_width)
        self.window.setGeometry(current)
        return True

    def handle(self, message):
        if not self.ready:
            return None
        msg = W.MSG.from_address(int(message))
        kind, value = msg.message, msg.wParam
        if kind == 0x112 and self.horizontal_restore:  # WM_SYSCOMMAND
            command = value & 0xfff0
            if command == 0xf120 and not self.user.IsZoomed(self.hwnd):  # SC_RESTORE
                self.restore_horizontal()
                return 0
            if command == 0xf030:  # Maximize must remember the original normal width.
                self.restore_horizontal()
        if kind == 0xa1 and value == HTCAPTION and self.horizontal_restore:
            local = self.point(msg.lParam)
            self.restore_horizontal(self.window.mapToGlobal(local).x())
        if kind == 0x7c and ctypes.c_ssize_t(value).value == -16:  # WM_STYLECHANGING / GWL_STYLE
            change = StyleChange.from_address(msg.lParam)
            change.new = (change.new & ~0x80c00000) | 0x000f0000
            return 0
        if kind in (0x83,0x84,0x86,0x317) and kind not in self.seen:
            self.seen.add(kind)
            log.info('Native frame message received: %#x',kind)
        if kind == 0x317:  # WM_PRINT: captures must not synthesize a legacy caption.
            return self.user.DefWindowProcW(self.hwnd,kind,value,msg.lParam & ~0x2)
        if kind == 0x85:  # WM_NCPAINT: the title bar is entirely client drawn.
            return 0
        if kind == 0x86:  # WM_NCACTIVATE without painting a second system caption.
            return 1
        if kind == 0x83 and value:  # WM_NCCALCSIZE: first member is rgrc[0].
            if self.user.IsZoomed(self.hwnd):
                rect = W.RECT.from_address(msg.lParam)
                work = self.monitor().work
                rect.left,rect.top,rect.right,rect.bottom = work.left,work.top,work.right,work.bottom
            return 0
        if kind == 0x24:  # WM_GETMINMAXINFO, respecting this monitor's taskbar.
            info = MinMaxInfo.from_address(msg.lParam)
            monitor = self.monitor()
            work, area = monitor.work,monitor.monitor
            info.maxPosition = W.POINT(work.left-area.left,work.top-area.top)
            info.maxSize = W.POINT(work.right-work.left,work.bottom-work.top)
            ratio = self.window.devicePixelRatioF()
            info.minTrack = W.POINT(round(self.window.minimumWidth()*ratio),round(self.window.minimumHeight()*ratio))
            return 0
        if kind == 0x84:  # WM_NCHITTEST: HTMAXBUTTON enables Windows Snap Layouts.
            return self.hit_test(self.point(msg.lParam))
        if kind in (0xa0,0x2a2):  # WM_NCMOUSEMOVE / WM_NCMOUSELEAVE
            title = getattr(self.window,'title_bar',None)
            if title:
                over = kind == 0xa0 and value == HTMAXBUTTON
                if title.max_button.property('nativeHover') != over:
                    title.max_button.setProperty('nativeHover',over)
                    title.max_button.style().polish(title.max_button)
                    title.max_button.update()
                if over:
                    track = TrackMouse(ctypes.sizeof(TrackMouse),0x12,self.hwnd,0)
                    self.user.TrackMouseEvent(ctypes.byref(track))
                elif not self.max_pressed:
                    title.max_button.setDown(False)
            result = ctypes.c_ssize_t()
            self.dwm.DwmDefWindowProc(self.hwnd,kind,value,msg.lParam,ctypes.byref(result))
        if kind == 0xa1 and value == HTMAXBUTTON:
            self.max_pressed = True
            self.window.title_bar.max_button.setDown(True)
            self.user.SetCapture(self.hwnd)
            return 0
        if kind in (0xa2,0x202) and self.max_pressed:
            point = self.point(msg.lParam) if kind == 0xa2 else QPoint(
                round(ctypes.c_short(msg.lParam & 0xffff).value/self.window.devicePixelRatioF()),
                round(ctypes.c_short((msg.lParam >> 16) & 0xffff).value/self.window.devicePixelRatioF()))
            clicked = self.hit_test(point) == HTMAXBUTTON
            self.max_pressed = False
            self.window.title_bar.max_button.setDown(False)
            self.user.ReleaseCapture()
            if clicked:
                QTimer.singleShot(0,self.window.title_bar.maximize)
            return 0
        if kind == 0x215 and self.max_pressed:  # WM_CAPTURECHANGED
            self.max_pressed = False
            self.window.title_bar.max_button.setDown(False)
        if kind == 0xa3 and value in (HTLEFT,HTRIGHT):  # Extra horizontal-axis double-click.
            self.stretch_horizontal()
            return 0
        if kind in (0xa1,0xa3) and value in (HTCAPTION,HTLEFT,HTRIGHT,HTTOP,HTBOTTOM,HTTOPLEFT,HTTOPRIGHT,HTBOTTOMLEFT,HTBOTTOMRIGHT):
            return self.user.DefWindowProcW(self.hwnd,kind,value,msg.lParam)
        if kind == 0x47:  # Let Qt/DefWindowProc generate WM_SIZE/WM_MOVE once.
            if not self.corner_pending:
                self.corner_pending = True
                QTimer.singleShot(0,self.update_corners)
        return None
