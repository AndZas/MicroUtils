"""Shared Qt surfaces, vector controls and native Windows backdrop.

No wallpaper capture, full-window blur effect or continuous repaint loop: DWM
composes the backdrop; Qt repaints only damaged regions and active controls.
"""
import ctypes
from ctypes import wintypes
import sys
import logging
from functools import lru_cache

from PySide6.QtCore import Qt, QRectF, QPointF, QPoint, QSize, Signal, Property, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QIcon, QPixmap, QImage, QPen, QPolygonF, QCursor
from app_icons import microphone_image
from window_frame import NativeFrame
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QWidget, QAbstractButton, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QComboBox, QDoubleSpinBox, QSpinBox, QVBoxLayout, QListView,
    QStyledItemDelegate, QStyle, QStyleOptionButton, QStyleOptionComboBox, QStylePainter, QSplitter, QSplitterHandle, QSlider)

TEXT, MUTED, GREEN, RED, AMBER = '#edf3fa', '#a2afc4', '#7de2bc', '#ff929d', '#f5cc88'
from version import APP_VERSION,DISPLAY_NAME
SIDEBAR_COMPACT, SIDEBAR_EXPANDED = 76, 224
NAV_ICON_LEFT, NAV_ICON_SIZE = 15, 22
ICON_OPTICAL_OFFSET = -.75
# Optical centres of the SVG artwork, rather than of its transparent canvas.
ICON_CENTRE_Y = {'input_settings':12, 'outputs':13, 'bell':11.5, 'settings':11, 'log':12.5}

STYLE = """
QWidget { color: #edf3fa; font-family: 'Segoe UI'; font-size: 13px; }
QWidget#sidebar { background: rgba(16,24,39,100); border-right: 1px solid rgba(176,203,255,22); }
QLabel { background: transparent; border: none; }
QLabel[role='muted'] { color: #a2afc4; }
QLabel[role='eyebrow'] { color: #8fabc4; font-size: 10px; font-weight: 600; letter-spacing: 1px; }
QLabel[role='title'] { font-size: 28px; font-weight: 600; }
QLabel[role='cardtitle'] { font-size: 15px; font-weight: 600; }
QFrame#card { background: rgba(39,53,75,165); border: 1px solid rgba(176,203,255,30); border-radius: 18px; }
QFrame#hero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(62,111,115,190),stop:1 rgba(44,60,91,175)); border: 1px solid rgba(143,231,208,55); border-radius: 18px; }
QPushButton { background: rgba(130,156,197,20); border: 1px solid rgba(176,203,255,36); border-radius: 9px; padding: 9px 13px; outline: none; }
QPushButton:hover { background: rgba(130,156,197,42); border-color: rgba(176,203,255,70); }
QPushButton:pressed { background: rgba(130,156,197,65); }
QPushButton:disabled { color: #7e8aa0; background: rgba(100,120,150,12); }
QPushButton[accent='true'] { background: #82dfbd; color: #152d2c; border-color: #82dfbd; font-weight: 600; }
QPushButton[accent='true']:hover { background: #a0edd0; }
QPushButton[nav='true'] { text-align: left; padding: 13px 15px; border: 1px solid transparent; background: transparent; color: #a2afc4; }
QPushButton[nav='true']:hover { background: rgba(145,174,222,20); color: #edf3fa; }
QPushButton[nav='true']:checked { background: rgba(125,226,188,23); border-color: rgba(125,226,188,33); color: #a6f1d6; }
QPushButton[caption='true'] { border: none; background: transparent; border-radius: 7px; padding: 0; }
QPushButton[caption='true']:hover { background: rgba(170,190,220,25); }
QPushButton[caption='true'][nativeHover='true'] { background: rgba(170,190,220,25); }
QPushButton#closeCaption:hover { background: #ad4759; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox { background: rgba(14,23,39,110); border: 1px solid rgba(176,203,255,45); border-radius: 8px; padding: 9px 10px; selection-background-color: #487b76; min-height: 18px; }
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus { border-color: rgba(176,203,255,95); }
QComboBox:focus { border-color: rgba(176,203,255,45); }
QComboBox:hover, QDoubleSpinBox:hover, QSpinBox:hover { background: rgba(70,88,115,65); }
QLineEdit:read-only { color: #a2afc4; }
QComboBox { padding-right: 28px; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: #25344c; color: #edf3fa; selection-background-color: #375650; border: 1px solid #4a6177; padding: 5px; outline: none; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { width: 20px; border: none; background: rgba(140,170,210,10); }
QAbstractSpinBox { padding-left: 14px; padding-right: 24px; }
QAbstractSpinBox QLineEdit { background: transparent; border: none; padding: 0; min-height: 0; }
QPushButton#hotkeyCapture { text-align: left; font-size: 13px; padding: 9px 13px; border-radius: 9px; }
QPushButton#hotkeyCapture[recording='true'] { background: rgba(125,226,188,24); }
QPushButton#muteLogo { background: transparent; border: none; padding: 0; border-radius: 12px; }
QPushButton#muteLogo:hover { background: rgba(125,226,188,22); }
QPushButton#filePicker { background: rgba(14,23,39,110); border: 1px solid rgba(176,203,255,45); border-radius: 8px; padding: 9px 12px; }
QPushButton#filePicker:hover { background: rgba(70,88,115,65); border-color: rgba(176,203,255,60); }
QPushButton#filePicker:focus { border-color: rgba(176,203,255,45); }
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: transparent; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 4px 0; }
QScrollBar::handle:vertical { background: rgba(162,182,214,65); border-radius: 3px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: rgba(162,182,214,115); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { color: #edf3fa; background: #25344c; border: 1px solid #4a6177; padding: 7px; }
"""

PATHS = {
    'speaker': '<path d="M11 4 6 8H3v8h3l5 4zM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
    'speaker_off': '<path d="M11 4 6 8H3v8h3l5 4zM16 9l6 6m0-6-6 6"/>',
    'play': '<path d="m8 5 11 7-11 7z"/>',
    'folder': '<path d="M3 8V5a2 2 0 0 1 2-2h4l3 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8z"/>',
    'input_settings': '<path d="M3 6h5m4 0h9M3 12h11m4 0h3M3 18h3m4 0h11"/><circle cx="10" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="8" cy="18" r="2"/>',
    'mic': '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/>',
    'outputs': '<rect x="3" y="4" width="18" height="13" rx="3"/><path d="M8 22h8m-4-5v5M7 9h10m-10 4h6"/>',
    'bell': '<path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5zm5 4h4M12 2v2"/>',
    'settings': '<path d="m9 3 .6-2h4.8l.6 2 2 1.2 2.1-.4 2.4 4.2-1.4 1.6v2.4l1.4 1.6-2.4 4.2-2.1-.4L15 19l-.6 2H9.6L9 19l-2-1.2-2.1.4-2.4-4.2L4 12.4V10L2.5 8.4l2.4-4.2 2.1.4z"/><circle cx="12" cy="11" r="3.2"/>',
    'keyboard': '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 12h.01M10 12h.01M14 12h.01M18 12h.01M7 16h10"/>',
    'log': '<rect x="5" y="3" width="14" height="19" rx="2"/><path d="M9 8h6m-6 5h6m-6 5h4"/>',
    'min': '<path d="M5 12h14"/>',
    'max': '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    'close': '<path d="m6 6 12 12M6 18 18 6"/>',
    'arrow': '<path d="M5 12h14m-5-5 5 5-5 5"/>',
}
PATHS['mic_off'] = PATHS['mic'] + '<path d="m3 3 18 18"/>'


@lru_cache(maxsize=64)
def vector_icon(name, color=MUTED, size=22):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
           f'<g fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
           + PATHS[name] + '</g></svg>').encode()
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(svg).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


@lru_cache(maxsize=16)
def application_icon(state=False, dot_color=GREEN):
    rgba = microphone_image(state, dot_color, 192)
    image = QImage(rgba.tobytes(), rgba.width, rgba.height, QImage.Format.Format_RGBA8888).copy()
    return QIcon(QPixmap.fromImage(image))


def centred_baseline(painter, text, height):
    return height/2 - QRectF(painter.fontMetrics().tightBoundingRect(text)).center().y()


def native_backdrop(widget):
    """Windows 11 22H2+ Desktop Acrylic; older systems use our opaque paint."""
    if sys.platform != 'win32':
        return False
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.WinDLL('dwmapi')
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        for attribute, value in ((20, 1), (33, 2)):
            data = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), 4)
        material = ctypes.c_int(3)  # DWMSBT_TRANSIENTWINDOW = Desktop Acrylic
        result = dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(material), 4)
        if result != 0:
            return False
        class MARGINS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int) for name in ('left', 'right', 'top', 'bottom')]
        dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(MARGINS)]
        margins = MARGINS(-1, -1, -1, -1)
        return dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins)) == 0
    except (OSError, AttributeError):
        return False


class GlassWindow(QWidget):
    def __init__(self, tool=False, popup=False, parent=None):
        flags = (Qt.WindowType.FramelessWindowHint | (Qt.WindowType.Popup if popup else Qt.WindowType.Tool)) if tool or popup else (
            Qt.WindowType.Window | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowCloseButtonHint)
        if tool:
            flags |= Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if tool:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(STYLE)
        self.backdrop = False
        self.tool_surface = tool or popup
        self.square_corners = False
        self.frame = None if self.tool_surface else NativeFrame(self)
        self.setMouseTracking(True)

    def showEvent(self, event):
        if self.frame:
            # Qt must finish ShowWindow before replacing its frameless style.
            QTimer.singleShot(0,self.frame.configure)
        self.backdrop = native_backdrop(self)
        if self.frame and self.frame.ready:
            self.frame.update_corners()
        self.update()
        super().showEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        # A stable dark tint also when Windows renders the bright/inactive acrylic
        # variant. Native clipping owns the outer corners: no second border/halo.
        alpha = 232 if self.backdrop else 255
        gradient.setColorAt(0, QColor(25, 38, 58, alpha))
        gradient.setColorAt(.6, QColor(25, 34, 52, alpha))
        gradient.setColorAt(1, QColor(35, 48, 66, alpha))
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.backdrop or self.square_corners or self.isMaximized():
            painter.drawRect(self.rect())
        else:
            painter.drawRoundedRect(QRectF(self.rect()), 12, 12)

    def nativeEvent(self, event_type, message):
        frame = getattr(self,'frame',None)
        if frame:
            try:
                result = frame.handle(message)
                if result is not None:
                    return True,result
            except (OSError, RuntimeError):
                logging.getLogger('VoicemeeterMuteTray').exception('Native window event failed')
        return super().nativeEvent(event_type, message)


class TitleBar(QWidget):
    def __init__(self, window, title):
        super().__init__(window)
        self.window = window
        window.title_bar = self
        self.buttons = []
        self.setFixedHeight(48)
        row = QHBoxLayout(self)
        row.setContentsMargins(24, 0, 12, 0)
        label = QLabel(title)
        label.setProperty('role', 'muted')
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(label)
        row.addStretch()
        for name, action in (('min', window.showMinimized), ('max', self.maximize), ('close', window.close)):
            button = QPushButton()
            button.setIcon(vector_icon(name, TEXT, 16))
            button.setFixedSize(34, 30)
            button.setProperty('caption', True)
            self.buttons.append(button)
            if name == 'max':
                self.max_button = button
            button.setAccessibleName({'min':'Свернуть', 'max':'Развернуть', 'close':'Закрыть'}[name])
            if name == 'close':
                button.setObjectName('closeCaption')
            button.clicked.connect(action)
            row.addWidget(button)

    def maximize(self):
        if not self.window.isMaximized() and self.window.frame:
            self.window.frame.restore_horizontal()
        self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not (self.window.frame and self.window.frame.ready):
            self.window.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize()


class Toggle(QAbstractButton):
    def __init__(self, title='', parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(44, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(title)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._position = 0.0
        self.animation = QPropertyAnimation(self, b'position', self)
        self.animation.setDuration(140)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self.animate)

    def get_position(self):
        return self._position

    def set_position(self, value):
        self._position = value
        self.update()

    position = Property(float, get_position, set_position)

    def sync(self, value):
        if self.isChecked() == bool(value):
            return
        self.blockSignals(True)
        self.setChecked(bool(value))
        self.blockSignals(False)
        self.animation.stop()
        self.set_position(float(bool(value)))

    def animate(self, checked):
        self.animation.stop()
        self.animation.setStartValue(self._position)
        self.animation.setEndValue(float(checked))
        self.animation.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._position
        painter.setPen(Qt.PenStyle.NoPen)
        hover = self.underMouse() and self.isEnabled()
        painter.setBrush(QColor(round(70+55*t+(12+17*t if hover else 0)),
            round(88+138*t+(16-5*t if hover else 0)),round(113+75*t+(17+5*t if hover else 0))))
        painter.drawRoundedRect(QRectF(1, 3, 42, 22), 11, 11)
        painter.setBrush(QColor('#183831' if self.isChecked() else '#e2eaf5'))
        painter.drawEllipse(QRectF(4+20*t, 6, 16, 16))


class SpeakerButton(QAbstractButton):
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(30,30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('Звук включён')
        self.toggled.connect(self.update)

    def enterEvent(self,event):
        super().enterEvent(event);self.update()

    def leaveEvent(self,event):
        super().leaveEvent(event);self.update()

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.underMouse():
            painter.setBrush(QColor(145,174,222,32 if not self.isDown() else 48))
            painter.drawRoundedRect(self.rect(),7,7)
        vector_icon('speaker' if self.isChecked() else 'speaker_off',GREEN if self.isChecked() else MUTED,18).paint(painter,6,6,18,18)


class VolumeSlider(QSlider):
    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(0,100)
        self.setFixedHeight(30)
        self.setMinimumWidth(62)
        self.setMaximumWidth(124)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.audible=True

    def enterEvent(self,event):
        super().enterEvent(event);self.update()

    def leaveEvent(self,event):
        super().leaveEvent(event);self.update()

    def set_at(self,event):
        fraction=max(0,min(1,(event.position().x()-7)/max(1,self.width()-14)))
        self.setValue(round(fraction*100))

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.setSliderDown(True);self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.set_at(event);event.accept()
        else:super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.isSliderDown():self.set_at(event);event.accept()
        else:super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.set_at(event);self.setSliderDown(False);event.accept()
        else:super().mouseReleaseEvent(event)

    def wheelEvent(self,event):
        event.ignore()

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered=self.underMouse() or self.isSliderDown()
        track=QColor('#687d98' if hovered else '#485c76')
        fill=QColor(('#a1eed0' if hovered else GREEN) if self.audible else ('#a2afc4' if hovered else '#798aa2'))
        left,right=7,self.width()-7
        x=left+(right-left)*self.value()/100
        painter.setPen(QPen(track,4,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(left,15),QPointF(right,15))
        painter.setPen(QPen(fill,4,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(left,15),QPointF(x,15))
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(fill)
        radius=6 if hovered else 5
        painter.drawEllipse(QPointF(x,15),radius,radius)


class SoundLevel(QWidget):
    volumeChanged=Signal(int)
    enabledChanged=Signal(bool)

    def __init__(self,title,volume,enabled):
        super().__init__()
        self.setFixedWidth(180)
        row=QHBoxLayout(self);row.setContentsMargins(0,0,0,0);row.setSpacing(6)
        self.mute=SpeakerButton()
        self.slider=VolumeSlider()
        self.slider.setAccessibleName(title+' — громкость')
        self.value_label=QLabel()
        self.value_label.setFixedWidth(32)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setStyleSheet('color: '+MUTED+'; font-size: 11px;')
        row.addWidget(self.mute);row.addWidget(self.slider,1);row.addWidget(self.value_label)
        self.slider.valueChanged.connect(self.volumeChanged)
        self.mute.clicked.connect(self.enabledChanged)
        self.sync(volume,enabled)

    def sync(self,volume,enabled):
        self.slider.blockSignals(True);self.slider.setValue(volume);self.slider.blockSignals(False)
        self.mute.setChecked(enabled)
        self.mute.setAccessibleName('Отключить звук' if enabled else 'Включить звук')
        self.slider.audible=enabled
        self.slider.update()
        self.value_label.setText(str(volume)+'%')


class StatusPill(QWidget):
    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        self.dot, self.label = QLabel('●'), QLabel()
        self.label.setWordWrap(True)
        self.dot.setFixedWidth(10)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        row.addWidget(self.dot)
        row.addWidget(self.label)
        self.value = None

    def align_navigation(self, compact):
        self.layout().setContentsMargins(0 if compact else NAV_ICON_LEFT, 0, 0, 0)
        self.layout().setSpacing(9)
        self.dot.setFixedWidth(22)
        self.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_status(self, text, color):
        if self.value == (text, color):
            return
        self.value = text, color
        self.dot.setStyleSheet('color: '+color+'; font-size: 12px;')
        self.label.setText(text)


class ConnectionButton(QAbstractButton):
    """An accessible action that deliberately paints no hover/pressed surface."""
    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.status = StatusPill()
        self.status.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.label, self.dot = self.status.label, self.status.dot
        row.addWidget(self.status)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('Открыть микшер VoiceMeeter Banana')

    def align_navigation(self, compact):
        self.status.align_navigation(compact)

    def set_status(self, text, color):
        self.status.set_status(text, color)
        self.setAccessibleDescription(text)

    def paintEvent(self, event):
        pass


def chevron(painter, x, y, down=True, size=4):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(MUTED), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    sign = 1 if down else -1
    painter.drawPolyline(QPolygonF([QPointF(x-size, y-sign*size/2), QPointF(x, y+sign*size/2), QPointF(x+size, y-sign*size/2)]))


class ChoiceBox(QComboBox):
    aboutToOpen = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.popup = None
        self.refreshing = False

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(80)
        return hint

    def showPopup(self):
        if self.popup is None:
            self.popup = ChoicePopup(self)
        self.aboutToOpen.emit()
        self.popup.open()

    def hidePopup(self):
        if self.popup:
            self.popup.hide()

    def set_refreshing(self, refreshing):
        self.refreshing = refreshing
        if self.popup:
            self.popup.loading.setVisible(refreshing)
            self.popup.view.setEnabled(not refreshing)
            if self.popup.isVisible():
                self.popup.position()

    def paintEvent(self, event):
        # These are selectors, not editable fields. Keep keyboard focus without
        # painting the native focus rectangle or an input-like accent border.
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
        chevron(painter, self.width()-16, self.height()/2)

    def wheelEvent(self, event):
        event.ignore()  # Scrolling a settings page must not change saved choices.


class NumberArrows:
    def __init__(self, parent=None):
        super().__init__(parent)
        # QAbstractSpinBox defaults to WheelFocus, which focuses a field before
        # our ignored wheel event can reach the surrounding settings page.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.lineEdit().setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.lineEdit().setTextMargins(10, 0, 4, 0)
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        chevron(painter, self.width()-11, self.height()*.28, False, 3)
        chevron(painter, self.width()-11, self.height()*.72, True, 3)

    def wheelEvent(self, event):
        # Also end an existing edit before scrolling the page. This handles
        # native focus changes made before wheel delivery without a stale ring.
        self.clearFocus()
        self.lineEdit().clearFocus()
        event.ignore()


class DecimalBox(NumberArrows, QDoubleSpinBox):
    pass


class CountBox(NumberArrows, QSpinBox):
    pass


class RoundedItem(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(120, 40)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(125, 226, 188, 46 if selected else 22))
            painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QColor(GREEN if selected else TEXT))
        text = painter.fontMetrics().elidedText(str(index.data() or ''), Qt.TextElideMode.ElideRight, int(rect.width())-24)
        painter.drawText(rect.adjusted(12, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()


class ChoicePopup(GlassWindow):
    def __init__(self, owner):
        super().__init__(popup=True, parent=owner)
        self.owner = owner
        self.above = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(4)
        self.loading = QLabel('Обновление списка…')
        self.loading.setStyleSheet('color: '+MUTED+'; padding: 8px;')
        layout.addWidget(self.loading)
        self.view = QListView()
        self.view.setModel(owner.model())
        self.view.setItemDelegate(RoundedItem(self.view))
        self.view.setMouseTracking(True)
        self.view.setUniformItemSizes(True)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setStyleSheet('QListView { background: transparent; border: none; outline: none; padding: 0; margin: 0; }')
        self.view.viewport().setAutoFillBackground(False)
        self.view.clicked.connect(self.choose)
        self.view.activated.connect(self.choose)
        layout.addWidget(self.view)

    def choose(self, index):
        if index.isValid() and not self.owner.refreshing:
            self.owner.setCurrentIndex(index.row())
            self.owner.activated.emit(index.row())
            self.hide()
            self.owner.setFocus()

    def position(self):
        area = self.owner.screen().availableGeometry()
        width = min(area.width()-24, max(220, self.owner.width()))
        height = min(8, max(1, self.owner.count()))*40+14+(38 if self.owner.refreshing else 0)
        height = min(height, area.height()-24)
        self.setFixedSize(width, height)
        pos = self.owner.mapToGlobal(QPoint(0, self.owner.height()+6))
        if self.above is None:
            self.above = pos.y()+height > area.bottom()-8
        if self.above:
            pos.setY(self.owner.mapToGlobal(QPoint()).y()-height-6)
        pos.setX(max(area.left()+8, min(pos.x(), area.right()-width-8)))
        pos.setY(max(area.top()+8, pos.y()))
        self.move(pos)

    def open(self):
        self.above = None
        self.loading.setVisible(self.owner.refreshing)
        self.view.setEnabled(not self.owner.refreshing)
        self.view.setCurrentIndex(self.owner.model().index(self.owner.currentIndex(), 0))
        self.position()
        self.show()
        self.view.setFocus()
        self.view.scrollTo(self.view.currentIndex())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.owner.setFocus()
        else:
            super().keyPressEvent(event)


class NavButton(QAbstractButton):
    def __init__(self, text, icon, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setAccessibleName(text)
        self.icon_name, self.compact = icon, False
        self.setCheckable(True)
        self.setFixedHeight(48)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.keyboard_focus = False

    def focusInEvent(self, event):
        self.keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        self.keyboard_focus = False
        super().focusOutEvent(event)
        self.update()

    def icon_rect(self):
        centre = ICON_CENTRE_Y.get(self.icon_name, 12)
        return QRectF(NAV_ICON_LEFT, self.height()/2-centre*NAV_ICON_SIZE/24+ICON_OPTICAL_OFFSET,
                      NAV_ICON_SIZE, NAV_ICON_SIZE)

    def sizeHint(self):
        return QSize(60 if self.compact else 180, 48)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        hovered = self.window().isActiveWindow() and self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if self.isChecked() or hovered:
            painter.setBrush(QColor(125, 226, 188, 25 if self.isChecked() else 13))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 11, 11)
        color = GREEN if self.isChecked() else MUTED
        rect = self.icon_rect()
        painter.save()
        painter.translate(rect.topLeft())
        vector_icon(self.icon_name, color).paint(painter, 0, 0, NAV_ICON_SIZE, NAV_ICON_SIZE)
        painter.restore()
        if not self.compact:
            painter.setPen(QColor(color))
            baseline = centred_baseline(painter, self.text(), self.height())
            painter.drawText(QPointF(46, baseline), self.text())


class SidebarHandle(QSplitterHandle):
    released = Signal()
    dragged = Signal(int)

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.dragging = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.grabMouse()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.dragging:
            x = self.parentWidget().mapFromGlobal(event.globalPosition().toPoint()).x()
            self.dragged.emit(x)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.cancel_drag()
        self.released.emit()

    def cancel_drag(self):
        self.dragging = False
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()


class SidebarSplitter(QSplitter):
    released = Signal()
    dragged = Signal(int)

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setHandleWidth(6)
        self.setChildrenCollapsible(False)

    def createHandle(self):
        handle = SidebarHandle(self.orientation(), self)
        handle.released.connect(self.released)
        handle.dragged.connect(self.dragged)
        return handle

    def cancel_drag(self):
        if self.count() > 1:
            self.handle(1).cancel_drag()


class MicButton(QPushButton):
    """Mute colour and endpoint connectivity are independent indicators."""
    def __init__(self):
        super().__init__()
        self.connection = None

    def set_connection(self, text, color):
        if self.connection != (text, color):
            self.connection = text, color
            self.setAccessibleDescription(text)
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.connection:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor('#192638'),2))
            painter.setBrush(QColor(self.connection[1]))
            painter.drawEllipse(QPointF(38,38),4.5,4.5)


class HotkeyButton(QPushButton):
    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(80)
        return hint

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text, option.icon = '', QIcon()
        option.state &= ~QStyle.StateFlag.State_HasFocus
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        painter.save()
        painter.translate(14, (self.height()-16)/2+ICON_OPTICAL_OFFSET)
        self.icon().paint(painter, 0, 0, 16, 16)
        painter.restore()
        painter.setPen(QColor(TEXT))
        rect = self.rect().adjusted(38, 0, -12, 0)
        text = painter.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(QPointF(rect.left(), centred_baseline(painter, text, self.height())), text)


class ActionButton(QPushButton):
    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(min(80,hint.width()))
        return hint


class FileButton(QPushButton):
    """A file chooser action with middle elision, never an editable text field."""
    def __init__(self, title, path='', placeholder='Выбрать файл…'):
        super().__init__()
        self.title, self.placeholder = title, placeholder
        self.path = ''
        self.setObjectName('filePicker')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(38)
        self.set_path(path)

    def set_path(self, path):
        self.path = path
        self.setAccessibleName(self.title+': '+(path or self.placeholder))
        self.update()

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        vector_icon('folder', MUTED, 16).paint(painter, 12, (self.height()-16)//2, 16, 16)
        rect = self.rect().adjusted(38, 0, -12, 0)
        painter.setPen(QColor(TEXT if self.path else MUTED))
        text = painter.fontMetrics().elidedText(self.path or self.placeholder, Qt.TextElideMode.ElideMiddle, max(0,rect.width()))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, text)
