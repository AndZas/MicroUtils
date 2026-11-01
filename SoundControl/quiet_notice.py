"""Thread-safe Qt dispatcher and a small, non-activating glass notification."""
import logging
import queue
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
import sounds
from ui_theme import GlassWindow, vector_icon, GREEN, AMBER, DISPLAY_NAME


class QuietNotice:
    def __init__(self, settings, stop):
        self.settings, self.stop = settings, stop
        self.messages, self.ui_tasks = queue.SimpleQueue(), queue.SimpleQueue()
        self.qt = QApplication.instance() or QApplication([])
        self.qt.setApplicationName(DISPLAY_NAME)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setStyle('Fusion')
        self.qt.setFont(QFont('Segoe UI', 10))
        self.window = GlassWindow(tool=True)
        self.window.setWindowTitle(DISPLAY_NAME+' — уведомление')
        self.window.setFixedWidth(390)
        layout = QVBoxLayout(self.window)
        layout.setContentsMargins(22, 20, 18, 22)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setProperty('role', 'cardtitle')
        heading.addWidget(self.title, 1)
        close = QPushButton()
        close.setIcon(vector_icon('close', size=16))
        close.setAccessibleName('Закрыть уведомление')
        close.setProperty('caption', True)
        close.setFixedSize(28, 28)
        close.clicked.connect(self.hide)
        heading.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(heading)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setProperty('role', 'muted')
        layout.addWidget(self.body)
        self.hide_timer = QTimer(self.window)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)
        self.visible = False
        self.dispatcher = QTimer()
        self.dispatcher.setInterval(80)
        self.dispatcher.timeout.connect(self.poll)
        self.dispatcher.start()

    def post(self, title, body):
        self.messages.put((title, body))

    def call(self, callback):
        self.ui_tasks.put(callback)

    def hide(self):
        self.hide_timer.stop()
        self.window.hide()
        self.visible = False

    def show(self, title, body):
        self.hide()
        self.title.setText(title)
        self.title.setStyleSheet('color: '+(GREEN if 'снова' in title else AMBER))
        self.body.setText(body)
        self.window.adjustSize()
        area = self.qt.primaryScreen().availableGeometry()
        self.window.move(area.right()-self.window.width()-17, area.bottom()-self.window.height()-17)
        self.window.show()
        self.visible = True
        duration = 5 if 'снова' in title else self.settings.notice_seconds
        if duration > 0:
            self.hide_timer.start(round(duration*1000))

    def poll(self):
        if self.stop.is_set():
            self.dispatcher.stop()
            self.qt.quit()
            return
        while True:
            try:
                callback = self.ui_tasks.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                logging.getLogger('VoicemeeterMuteTray').exception('Window action failed')
        latest = None
        while True:
            try:
                latest = self.messages.get_nowait()
            except queue.Empty:
                break
        if latest and self.settings.notification_sound:
            sounds.play(self.settings.notification_sound_file,'notice',self.settings.notification_volume)
        if not self.settings.notifications:
            if self.visible:
                self.hide()
        elif latest:
            self.show(*latest)

    def run(self):
        self.qt.exec()
