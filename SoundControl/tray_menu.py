"""Qt tray icon and a glass popup sharing the application's visual language."""
from PySide6.QtCore import Qt, QPointF, QSize, QThread, QTimer
from PySide6.QtGui import QColor, QCursor, QImage, QPixmap, QIcon, QPainter, QPen
from PySide6.QtWidgets import QSystemTrayIcon, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QAbstractButton
from ui_theme import GlassWindow, vector_icon, centred_baseline, DISPLAY_NAME, APP_VERSION, TEXT, MUTED, GREEN

TRAY_TITLE = f'{DISPLAY_NAME} · v{APP_VERSION}'


class MenuAction(QAbstractButton):
    def __init__(self, text, icon, detail=''):
        super().__init__()
        self.setText(text)
        self.setAccessibleName(text)
        self.icon_name, self.detail = icon,detail
        self.setFixedHeight(42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.keyboard_focus = False

    def sizeHint(self):
        return QSize(302,42)

    def enterEvent(self,event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self,event):
        super().leaveEvent(event)
        self.update()

    def focusInEvent(self,event):
        self.keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                                                Qt.FocusReason.ShortcutFocusReason)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self,event):
        self.keyboard_focus = False
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self,event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        hovered = self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        if hovered or (self.hasFocus() and self.keyboard_focus):
            painter.setBrush(QColor(145,174,222,25 if not self.isDown() else 40))
            painter.drawRoundedRect(self.rect().adjusted(1,1,-1,-1),8,8)
        vector_icon(self.icon_name,MUTED,18).paint(painter,12,11,18,18)
        painter.setPen(QColor(TEXT))
        painter.drawText(QPointF(42,centred_baseline(painter,self.text(),self.height())),self.text())
        if self.detail:
            painter.setPen(QColor(MUTED))
            font = painter.font()
            font.setPixelSize(11)
            painter.setFont(font)
            width = painter.fontMetrics().horizontalAdvance(self.detail)
            painter.drawText(QPointF(self.width()-34-width,centred_baseline(painter,self.detail,self.height())),self.detail)
        if self.isCheckable():
            painter.setPen(QPen(QColor(GREEN if self.isChecked() else MUTED),1.4))
            painter.setBrush(QColor(GREEN) if self.isChecked() else Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(self.width()-17,21),3.5,3.5)


class TrayMenu(GlassWindow):
    def __init__(self,app):
        super().__init__(popup=True)
        self.app = app
        self.setWindowTitle(DISPLAY_NAME+' — меню')
        self.setFixedWidth(326)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10,10,10,10)
        layout.setSpacing(2)
        heading = QHBoxLayout()
        heading.setContentsMargins(12,5,12,9)
        name,version = QLabel(DISPLAY_NAME),QLabel('v'+APP_VERSION)
        version.setStyleSheet('color: '+MUTED+'; font-size: 11px;')
        heading.addWidget(name)
        heading.addStretch()
        heading.addWidget(version)
        layout.addLayout(heading)
        self.actions = []

        def action(text,icon,callback):
            button = MenuAction(text,icon)
            button.clicked.connect(lambda: (self.hide(),callback()))
            layout.addWidget(button)
            self.actions.append(button)
            return button

        def separator():
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet('background: rgba(176,203,255,30); border: none; margin-left: 10px; margin-right: 10px;')
            layout.addSpacing(5)
            layout.addWidget(line)
            layout.addSpacing(5)

        self.hotkey = action('Горячая клавиша','keyboard',app.toggle_hotkey)
        self.hotkey.setCheckable(True)
        action('Восстановить микрофон','mic',lambda: app.commands.put('repair'))
        separator()
        action('VoiceMeeter Banana','outputs',app.open_banana)
        action('Настройки','settings',app.show_settings)
        separator()
        action('Выход','close',app.exit)

    def update_state(self):
        self.hotkey.setChecked(self.app.enabled)
        self.hotkey.detail = self.app.settings.hotkey.title()
        self.hotkey.update()

    def open(self, position=None):
        for button in self.actions:
            button.clearFocus()
            button.keyboard_focus = False
            button.setDown(False)
        self.update_state()
        self.adjustSize()
        point = position or QCursor.pos()
        screen = self.screen().availableGeometry()
        # Clamp to the monitor containing the tray, including non-primary screens.
        from PySide6.QtGui import QGuiApplication
        target = QGuiApplication.screenAt(point)
        if target:
            screen = target.availableGeometry()
        x = min(max(screen.left()+6,point.x()-self.width()),screen.right()-self.width()-6)
        y = min(max(screen.top()+6,point.y()-self.height()),screen.bottom()-self.height()-6)
        self.move(x,y)
        self.show()
        self.raise_()
        self.activateWindow()

    def hideEvent(self,event):
        for button in self.actions:
            button.clearFocus()
            button.keyboard_focus = False
            button.setDown(False)
            button.update()
        super().hideEvent(event)

    def keyPressEvent(self,event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)


class TrayController:
    """Small thread-safe presentation adapter for the existing audio worker."""
    def __init__(self,app,image):
        self.app = app
        self.menu = TrayMenu(app)
        self.tray = QSystemTrayIcon()
        self.tray.setToolTip(TRAY_TITLE)
        self.tray.activated.connect(self.activated)
        self._icon, self._visible = None,False
        self.icon = image
        self.restore_timer = QTimer(self.tray)
        self.restore_timer.setSingleShot(True)
        self.restore_timer.timeout.connect(self.restore)

    def restore(self):
        """Re-register with Explorer after resume/recreation; preserve window-only mode."""
        self.menu.hide()
        self.tray.hide()
        if self._visible:
            self.tray.show()
            if not QSystemTrayIcon.isSystemTrayAvailable():
                self.restore_timer.start(2000)

    def dispatch(self,callback):
        if QThread.currentThread() == self.tray.thread():
            callback()
        else:
            self.app.notice.call(callback)

    @property
    def icon(self):
        return self._icon

    @icon.setter
    def icon(self,image):
        self._icon = image
        def apply():
            rgba = image.convert('RGBA')
            data = QImage(rgba.tobytes(),rgba.width,rgba.height,QImage.Format.Format_RGBA8888).copy()
            self.tray.setIcon(QIcon(QPixmap.fromImage(data)))
        self.dispatch(apply)

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self,value):
        self._visible = bool(value)
        self.dispatch(lambda: self.tray.setVisible(self._visible))

    def activated(self,reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self.menu.open()
        elif reason in (QSystemTrayIcon.ActivationReason.Trigger,QSystemTrayIcon.ActivationReason.DoubleClick):
            self.app.show_settings()

    def update_menu(self):
        self.dispatch(self.menu.update_state)

    def run_detached(self,setup):
        setup(self)

    def stop(self):
        self.dispatch(self.restore_timer.stop)
        self.visible = False
        self.dispatch(self.menu.hide)
