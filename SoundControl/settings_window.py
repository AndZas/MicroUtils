"""Qt settings shell. Audio, hooks and device I/O remain on the existing worker."""
import logging
from PySide6.QtCore import Qt, QTimer, QSize, QEvent
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QBoxLayout,
    QStackedWidget, QScrollArea, QFileDialog, QSizePolicy, QLayout)
from hotkeys import key_name, normalize_hotkey, MODIFIERS
from config import DEFAULTS
from ui_theme import (GlassWindow, TitleBar, Toggle, StatusPill, ConnectionButton, ChoiceBox, DecimalBox, CountBox,
    NavButton, SidebarSplitter, MicButton, HotkeyButton, FileButton, ActionButton, SoundLevel, DISPLAY_NAME, APP_VERSION, SIDEBAR_COMPACT, SIDEBAR_EXPANDED,
    vector_icon, application_icon, NAV_ICON_LEFT, NAV_ICON_SIZE, MUTED, GREEN, RED, AMBER)
from app_icons import connection_color
import sounds

log = logging.getLogger('VoicemeeterMuteTray')


class WrappingLabel(QLabel):
    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(min(80,hint.width()))
        return hint


def label(text, role=None):
    widget = WrappingLabel(text)
    widget.setWordWrap(True)
    if role:
        widget.setProperty('role', role)
    return widget


class SettingsShell(GlassWindow):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.setWindowTitle(DISPLAY_NAME)
        self.setMinimumSize(480, 420)
        self.resize(1080, 800)

    def closeEvent(self, event):
        event.ignore()
        self.manager.hide()

    def showEvent(self, event):
        super().showEvent(event)
        self.manager.timer.start()

    def hideEvent(self, event):
        self.manager.timer.stop()
        self.manager.cancel_capture()
        super().hideEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and hasattr(self.manager,'splitter'):
            self.manager.splitter.cancel_drag()
            self.manager.adapt_sidebar()
        if event.type() == QEvent.Type.ActivationChange and hasattr(self.manager,'journal'):
            for button in self.manager.nav_buttons+[self.manager.journal]:
                button.update()
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self.manager.timer.stop()
                self.manager.cancel_capture()
            elif self.isVisible():
                self.manager.timer.start()
        super().changeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self.manager, 'splitter'):
            self.manager.adapt_sidebar()


class SettingsWindow:
    def __init__(self, app, get_startup, toggle_startup, image):
        self.app, self.get_startup, self.toggle_startup, self.image = app, get_startup, toggle_startup, image
        self.window = self.timer = None
        self.recording = False
        self.capture_timer = None
        self.switches, self.mic_options = [], []
        self.sound_levels=[]
        self.compact = None
        self.preferred_sidebar = SIDEBAR_COMPACT if app.settings.sidebar_width <= 150 else SIDEBAR_EXPANDED
        self.mic_busy = False
        self.icon_signature = None
        self.refresh_icon()

    def refresh_icon(self):
        recovery = self.app.recovery
        color = connection_color(self.app.state, getattr(recovery, 'was_present', None),
                                 getattr(recovery, 'pending', False))
        signature = self.app.state, color
        if signature != self.icon_signature:
            self.icon_signature = signature
            self.current_icon = application_icon(self.app.state)
            self.app.notice.qt.setWindowIcon(self.current_icon)
        if self.window:
            if self.window.windowIcon().cacheKey() != self.current_icon.cacheKey():
                self.window.setWindowIcon(self.current_icon)
            if self.logo.icon().cacheKey() != self.current_icon.cacheKey():
                self.logo.setIcon(self.current_icon)

    def feedback(self, text, error=False):
        if self.window:
            self.feedback_label.setText(text)
            self.feedback_label.setStyleSheet('color: '+(RED if error else MUTED))
            self.feedback_label.setVisible(bool(text))

    def guarded(self, action):
        try:
            action()
            self.feedback('')
        except (OSError, ValueError, TypeError) as exc:
            log.warning('Settings action failed: %s', exc)
            self.feedback(str(exc), True)
        finally:
            self.refresh_switches()

    def button(self, text, action, accent=False):
        button = ActionButton(text)
        button.setProperty('accent', accent)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        button.clicked.connect(action)
        return button

    def card(self, page, title, subtitle=None, hero=False):
        frame = QFrame()
        frame.setObjectName('hero' if hero else 'card')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)
        layout.addWidget(label(title, 'cardtitle'))
        if subtitle:
            layout.addWidget(label(subtitle, 'muted'))
        page.addWidget(frame)
        return layout

    def row(self, layout, *widgets, stretch=False):
        row = QHBoxLayout()
        row.setSpacing(10)
        for widget in widgets:
            row.addWidget(widget)
        if stretch:
            row.addStretch()
        layout.addLayout(row)
        return row

    def switch_row(self, layout, title, description, getter, action):
        row = QHBoxLayout()
        row.setSpacing(24)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        copy.addWidget(label(title))
        if description:
            copy.addWidget(label(description, 'muted'))
        row.addLayout(copy, 1)
        toggle = Toggle(title)
        toggle.sync(getter())
        toggle.clicked.connect(lambda: self.guarded(action))
        row.addWidget(toggle)
        layout.addLayout(row)
        self.switches.append((toggle, getter))
        return toggle

    def switch(self, layout, key, title, description=''):
        return self.switch_row(layout, title, description, lambda: getattr(self.app.settings, key),
            lambda: self.app.change_setting(key, not getattr(self.app.settings, key)))

    def combo(self, values, current, action=None):
        box = ChoiceBox()
        box.setSizeAdjustPolicy(ChoiceBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(12)
        for text, value in values:
            box.addItem(text, value)
        box.setCurrentIndex(box.findData(current))
        if action:
            box.activated.connect(lambda _: self.guarded(lambda: action(box.currentData())))
        return box

    def number(self, layout, title, key, low, high, step=.5, integer=False):
        field = CountBox() if integer else DecimalBox()
        if not integer:
            field.setDecimals(1)
        field.setRange(low, high)
        field.setSingleStep(step)
        field.setValue(getattr(self.app.settings, key))
        field.setFixedWidth(112)
        field.valueChanged.connect(lambda value: self.save_number(field, key, value))
        self.row(layout, label(title), field)
        return field

    def save_number(self, field, key, value):
        try:
            self.app.change_setting(key, value)
            self.feedback('')
        except (OSError, ValueError, TypeError) as exc:
            field.blockSignals(True)
            field.setValue(getattr(self.app.settings, key))
            field.blockSignals(False)
            self.feedback(str(exc), True)

    def file_field(self, layout, title, key, executable=False, preview=None):
        layout.addWidget(label(title, 'muted'))
        field = FileButton(title, getattr(self.app.settings,key),
            'Определять автоматически' if executable else 'Звук Sound Control')
        def choose():
            caption = 'Выберите VoiceMeeter Banana' if executable else 'Выберите WAV: '+title.lower()
            path, _ = QFileDialog.getOpenFileName(self.window, caption, '', 'Программа (*.exe)' if executable else 'Аудио WAV (*.wav)')
            if path:
                self.guarded(lambda: self.app.change_setting(key, path))
                field.set_path(getattr(self.app.settings, key))
                if executable:
                    self.feedback('Путь сохранён. Применится после перезапуска утилиты')
        def reset():
            self.guarded(lambda: self.app.change_setting(key, ''))
            field.set_path(getattr(self.app.settings, key))
        field.clicked.connect(choose)
        controls=[field]
        if preview:
            listen=self.button('',preview)
            listen.setIcon(vector_icon('play',size=16));listen.setFixedSize(38,38)
            listen.setAccessibleName('Прослушать: '+title)
            controls.append(listen)
        controls.append(self.button('Сброс',reset))
        self.row(layout,*controls)

    def sound_level(self,layout,title,description,enabled_key,volume_key):
        row=QHBoxLayout();row.setSpacing(12)
        copy=QVBoxLayout();copy.setSpacing(4)
        copy.addWidget(label(title));copy.addWidget(label(description,'muted'))
        row.addLayout(copy,1)
        control=SoundLevel(title,getattr(self.app.settings,volume_key),getattr(self.app.settings,enabled_key))
        def save_volume(value):
            # Dragging is immediate in the UI; persist once at release.
            control.value_label.setText(str(value)+'%')
            if not control.slider.isSliderDown():self.guarded(lambda:self.app.change_setting(volume_key,value))
        control.volumeChanged.connect(save_volume)
        control.slider.sliderReleased.connect(lambda:self.guarded(lambda:self.app.change_setting(volume_key,control.slider.value())))
        control.enabledChanged.connect(lambda enabled:self.guarded(lambda:self.app.change_setting(enabled_key,enabled)))
        row.addWidget(control)
        layout.addLayout(row)
        self.sound_levels.append((control,enabled_key,volume_key))
        return control

    def preview_sound(self,key,kind,enabled_key,volume_key):
        s=self.app.settings
        if getattr(s,enabled_key):sounds.play(getattr(s,key),kind,getattr(s,volume_key))

    def page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        layout.setContentsMargins(0, 0, 10, 10)
        layout.setSpacing(14)
        scroll.setWidget(content)
        self.stack.addWidget(scroll)
        return layout

    def build(self):
        self.timer = QTimer()
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.capture_timer = QTimer()
        self.capture_timer.setSingleShot(True)
        self.capture_timer.timeout.connect(self.cancel_capture)
        w = self.window = SettingsShell(self)
        w.setWindowIcon(self.current_icon)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        outer.addWidget(TitleBar(w, DISPLAY_NAME))
        self.splitter = SidebarSplitter()
        outer.addWidget(self.splitter, 1)
        self.sidebar = QWidget()
        self.sidebar.setObjectName('sidebar')
        self.sidebar.setMinimumWidth(76)
        self.side_layout = side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(12, 20, 12, 20)
        side.setSpacing(8)
        header = QWidget()
        self.header_layout = QHBoxLayout(header)
        self.header_layout.setContentsMargins(2, 0, 0, 0)
        self.header_layout.setSpacing(4)
        self.logo = MicButton()
        self.logo.clicked.connect(self.app.request_toggle)
        self.logo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.logo.setObjectName('muteLogo')
        self.logo.setFixedSize(48, 48)
        self.logo.setIconSize(QSize(36, 36))
        self.logo.setAccessibleName('Включить или выключить микрофон')
        self.header_layout.addWidget(self.logo)
        self.brand = label(DISPLAY_NAME)
        self.brand.setWordWrap(False)
        self.brand.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.brand.setStyleSheet('font-size: 18px; font-weight: 600;')
        self.header_layout.addWidget(self.brand, 1)
        side.addWidget(header)
        side.addSpacing(18)
        sections = [('Микрофон', 'input_settings', 'Клавиша, устройство и восстановление'),
                    ('Аудиовыходы', 'outputs', 'Автоподключение аппаратных выходов'),
                    ('Уведомления', 'bell', 'Ненавязчивые сообщения и звуки'),
                    ('Приложение', 'settings', 'Запуск, поведение окна и VoiceMeeter Banana')]
        self.nav_buttons = []
        for index, (title, icon, _) in enumerate(sections):
            button = NavButton(title, icon)
            button.clicked.connect(lambda checked=False, i=index: self.navigate(i))
            self.nav_buttons.append(button)
            side.addWidget(button)
        side.addStretch()
        self.sidebar_connection = ConnectionButton()
        self.sidebar_connection.clicked.connect(lambda: self.guarded(self.app.open_banana))
        side.addWidget(self.sidebar_connection)
        side.addSpacing(10)
        self.journal = NavButton('Журнал событий', 'log')
        self.journal.setCheckable(False)
        self.journal.clicked.connect(lambda: self.guarded(self.app.open_log))
        side.addWidget(self.journal)
        self.version_label = label('v'+APP_VERSION)
        self.version_label.setStyleSheet('color: #94a2b6; font-size: 12px;')
        self.version_label.setWordWrap(False)
        side.addWidget(self.version_label)
        self.splitter.addWidget(self.sidebar)
        content = QWidget()
        content.setMinimumWidth(0)
        right = QVBoxLayout(content)
        right.setContentsMargins(22, 18, 18, 18)
        right.setSpacing(15)
        self.splitter.addWidget(content)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(lambda pos, index: self.adapt_sidebar())
        self.splitter.dragged.connect(self.sidebar_dragged)
        self.splitter.released.connect(self.adapt_sidebar)
        self.page_title = label('', 'title')
        self.page_description = label('', 'muted')
        right.addWidget(self.page_title)
        right.addWidget(self.page_description)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        self.section_titles = sections
        mic, outputs, notices, application = [self.page() for _ in sections]
        self.build_microphone(mic)
        self.build_outputs(outputs)
        self.build_notices(notices)
        self.build_application(application)
        for page in (mic, outputs, notices, application):
            page.addStretch()
        self.feedback_label = label('', 'muted')
        self.feedback_label.setStyleSheet('font-size: 11px;')
        self.feedback_label.hide()
        footer = QHBoxLayout()
        footer.addWidget(self.button('Завершить работу', self.app.exit))
        footer.addWidget(self.feedback_label, 1)
        footer.addStretch()
        footer.addWidget(self.button('Готово', self.hide, True))
        right.addLayout(footer)
        self.escape = QShortcut(QKeySequence('Esc'), w)
        self.escape.activated.connect(lambda: self.cancel_capture() if self.recording else self.hide())
        self.navigate(0)
        self.catalog_updated()
        self.adapt_sidebar()
        area = self.app.notice.qt.primaryScreen().availableGeometry()
        w.resize(min(1080, area.width()-60), min(800, area.height()-60))
        w.move(area.center()-w.rect().center())

    def build_microphone(self, page):
        s = self.app.settings
        card = self.card(page, 'Текущее устройство', hero=True)
        card.setContentsMargins(22, 16, 22, 16)
        card.setSpacing(10)
        self.device_label = label('', 'cardtitle')
        card.addWidget(self.device_label)
        self.mute_status, self.connection_status = StatusPill(), StatusPill()
        self.status_row = status_row = self.row(card, self.mute_status, self.connection_status, stretch=True)
        restore = self.button('Восстановить', lambda: self.app.commands.put('repair'))
        restore.setFixedHeight(32)
        status_row.addWidget(restore)
        for widget in (self.mute_status, self.connection_status, restore):
            status_row.setAlignment(widget, Qt.AlignmentFlag.AlignBottom)
        card = self.card(page, 'Горячая клавиша')
        self.switch_row(card, 'Переключение микрофона', 'Mute выбранного входа Banana из любой программы',
                        lambda: self.app.enabled, self.app.toggle_hotkey)
        self.record_button = HotkeyButton(self.pretty_hotkey(s.hotkey))
        self.record_button.clicked.connect(self.begin_capture)
        self.record_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.record_button.setObjectName('hotkeyCapture')
        self.record_button.setIcon(vector_icon('keyboard'))
        self.record_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.record_button.setFixedHeight(38)
        reset = self.button('Сбросить', self.reset_hotkey)
        reset.setFixedHeight(38)
        self.row(card, self.record_button, reset)
        self.capture_hint = label('Нажмите на сочетание, затем нажмите и отпустите нужные клавиши.', 'muted')
        card.addWidget(self.capture_hint)
        card = self.card(page, 'Устройство и вход', 'Выбранный микрофон сохраняется между запусками.')
        self.strip_combo = self.combo([(f'Вход {i+1}', i) for i in range(3)], s.strip_index)
        self.strip_combo.activated.connect(lambda _: self.configure_microphone(None))
        self.row(card, label('Аппаратный вход Banana'), self.strip_combo)
        self.mic_combo = self.combo([], None)
        self.mic_combo.aboutToOpen.connect(self.refresh_inputs)
        self.mic_combo.activated.connect(lambda _: self.select_microphone())
        card.addWidget(self.mic_combo)
        card.addWidget(label('При первом запуске используется устройство из Banana, а если оно не назначено — микрофон Windows по умолчанию. Ручной выбор назначает устройство через WDM.', 'muted'))
        card = self.card(page, 'Восстановление подключения')
        self.switch(card, 'auto_repair', 'Автовосстановление', 'Переназначать вход после возвращения микрофона и перезапускать аудиодвижок')
        self.mic_delay = self.number(card, 'Стабильное подключение, с', 'recovery_settle_seconds', .5, 15)
        self.mic_attempts = self.number(card, 'Максимум попыток', 'recovery_max_attempts', 1, 5, 1, integer=True)

    def build_outputs(self, page):
        s = self.app.settings
        card = self.card(page, 'Автоподключение выходов', 'После подключения устройства аудиодвижок перезапустится один раз, когда оно будет готово.', hero=True)
        self.switch(card, 'output_reconnect', 'Восстанавливать подключение', 'Пока устройство выключено, повторных перезапусков не будет')
        card = self.card(page, 'Аппаратные выходы', 'Устройства берутся из назначений A1 / A2 / A3 в Banana.')
        self.bus_switches, self.output_labels = [], []
        for bus in range(3):
            name = label('Загрузка…', 'muted')
            self.output_labels.append(name)
            row = QHBoxLayout()
            bus_label = label(f'A{bus+1}', 'cardtitle')
            bus_label.setFixedWidth(34)
            row.addWidget(bus_label)
            row.addWidget(name, 1)
            toggle = Toggle(f'Отслеживать A{bus+1}')
            toggle.sync(bus in s.output_bus_indices)
            toggle.clicked.connect(self.save_buses)
            row.addWidget(toggle)
            self.bus_switches.append(toggle)
            card.addLayout(row)
        card.addWidget(label('Тип устройств', 'muted'))
        card.addWidget(self.combo([('Только Bluetooth (A2DP)', 'bluetooth'), ('Любые аудиоустройства', 'all')],
            s.output_device_scope, lambda value: self.app.change_setting('output_device_scope', value)))
        card = self.card(page, 'Задержки', 'Близкие по времени подключения объединяются в один перезапуск.')
        self.output_delay = self.number(card, 'Ожидание подключения, с', 'output_settle_seconds', .5, 15)
        self.output_cooldown = self.number(card, 'Пауза между перезапусками, с', 'output_cooldown_seconds', 3, 120, 1)

    def build_notices(self, page):
        s = self.app.settings
        card = self.card(page, 'Тихие уведомления', 'Небольшая стеклянная панель у края экрана, которая не перехватывает фокус.', hero=True)
        self.switch(card, 'notifications', 'Показывать уведомления', 'Сообщать о потере микрофона и результате восстановления')
        self.notice_time = self.number(card, 'Длительность, с', 'notice_seconds', 0, 120, 1)
        card.addWidget(label('0 — предупреждение остаётся до закрытия. Сообщение о восстановлении исчезает через 5 секунд.', 'muted'))
        card = self.card(page, 'Звуки микрофона')
        self.mute_level=self.sound_level(card,'Звук при mute / unmute','Проигрывать после подтверждённого изменения состояния','mute_sound','mute_volume')
        self.file_field(card, 'Микрофон включён', 'sound_on_file',preview=lambda:self.preview_sound('sound_on_file','unmute','mute_sound','mute_volume'))
        self.file_field(card, 'Микрофон выключен', 'sound_off_file',preview=lambda:self.preview_sound('sound_off_file','mute','mute_sound','mute_volume'))
        card = self.card(page, 'Звук уведомлений')
        self.notification_level=self.sound_level(card,'Звуковой сигнал','Работает независимо от показа панели','notification_sound','notification_volume')
        self.file_field(card, 'Файл уведомления', 'notification_sound_file',preview=lambda:self.preview_sound('notification_sound_file','notice','notification_sound','notification_volume'))
        card.addWidget(label('Пустой путь — собственный звук Sound Control. Можно выбрать PCM WAV 8/16/24/32 бит до 32 МБ. Кнопка динамика отключает звук, сохраняя громкость.', 'muted'))

    def build_application(self, page):
        s = self.app.settings
        card = self.card(page, 'Окно и запуск')
        card.addWidget(label('Режим работы', 'muted'))
        card.addWidget(self.combo([('Только трей', 'tray'), ('Окно и трей', 'both'), ('Только окно', 'window')],
            s.ui_mode, lambda value: self.app.change_setting('ui_mode', value)))
        self.switch(card, 'close_to_tray', 'Закрывать окно в трей', 'В режиме «Только окно» крестик всегда завершает утилиту')
        self.switch_row(card, 'Запускать с Windows', 'С выбранными настройками окна и трея', self.get_startup, self.toggle_startup)
        card = self.card(page, 'VoiceMeeter Banana', hero=True)
        self.banana_label = label('', 'muted')
        card.addWidget(self.banana_label)
        self.switch(card, 'launch_banana', 'Запускать вместе с Sound Control', 'Уже работающий экземпляр используется повторно')
        self.switch(card, 'hide_banana_tray', 'Скрывать значок Banana', 'Открывайте микшер здесь; при выходе значок вернётся')
        self.row(card, self.button('Открыть микшер Banana', self.app.open_banana, True), stretch=True)
        card = self.card(page, 'Расположение Banana')
        self.file_field(card, 'Пустой путь — автоматический поиск установленной программы', 'banana_exe', executable=True)
        card.addWidget(label('Изменение пути применяется после перезапуска Sound Control. Чтобы подключаться к Banana вручную, отключите её запуск и скрытие значка.', 'muted'))
        from license_info import open_notices
        self.row(card,self.button('Лицензии компонентов',lambda:self.guarded(open_notices)),stretch=True)

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        self.fit_pages()
        self.stack.currentWidget().verticalScrollBar().setValue(0)
        self.page_title.setText(self.section_titles[index][0])
        self.page_description.setText(self.section_titles[index][2])
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
            button.update()

    def update_sidebar(self, compact):
        if self.compact == compact:
            return
        self.compact = compact
        self.brand.setVisible(not compact)
        self.sidebar_connection.label.setVisible(not compact)
        self.sidebar_connection.align_navigation(compact)
        self.side_layout.setAlignment(self.sidebar_connection, Qt.AlignmentFlag.AlignHCenter if compact else Qt.AlignmentFlag.AlignLeft)
        self.header_layout.setAlignment(self.logo, Qt.AlignmentFlag.AlignLeft)
        self.version_label.setText('v'+APP_VERSION.split('-')[0] if compact else 'v'+APP_VERSION)
        compact_width = self.version_label.fontMetrics().horizontalAdvance('v'+APP_VERSION.split('-')[0])
        version_left = round(NAV_ICON_LEFT+NAV_ICON_SIZE/2-compact_width/2)
        self.version_label.setContentsMargins(version_left, 0, 0, 0)
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        for button in self.nav_buttons+[self.journal]:
            button.compact = compact
            button.updateGeometry()
            button.update()

    def adapt_sidebar(self):
        width = SIDEBAR_COMPACT if self.window.width() < 880 or self.preferred_sidebar == SIDEBAR_COMPACT else SIDEBAR_EXPANDED
        self.update_sidebar(width == SIDEBAR_COMPACT)
        if hasattr(self,'status_row'):
            self.status_row.setDirection(QBoxLayout.Direction.TopToBottom if self.window.width()<640 else QBoxLayout.Direction.LeftToRight)
        if abs(self.splitter.sizes()[0]-width) > 1:
            self.splitter.setSizes([width, max(1, self.window.width()-width-8)])
        if hasattr(self,'stack'):
            self.fit_pages()

    def fit_pages(self):
        scroll = self.stack.currentWidget()
        if scroll:
            content = scroll.widget()
            content.layout().activate()
            content.resize(max(scroll.viewport().width(),content.minimumSizeHint().width()),content.height())

    def sidebar_dragged(self, position):
        midpoint = (SIDEBAR_COMPACT+SIDEBAR_EXPANDED)//2
        width = self.preferred_sidebar
        if position < midpoint-18:
            width = SIDEBAR_COMPACT
        elif position > midpoint+18:
            width = SIDEBAR_EXPANDED
        if width != self.preferred_sidebar:
            self.guarded(lambda: self.app.change_setting('sidebar_width', width))
            self.preferred_sidebar = self.app.settings.sidebar_width
        self.adapt_sidebar()

    def show(self):
        if self.window is None:
            self.build()
        if self.window.frame and not self.window.frame.ready:
            self.window.frame.configure()
        if self.window.isMinimized():
            self.window.showNormal()
        else:
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.app.commands.put('refresh_devices')
        self.refresh()

    def hide(self):
        self.cancel_capture()
        if self.app.settings.ui_mode == 'window' or not self.app.settings.close_to_tray:
            self.app.exit()
            return
        self.window.hide()

    def begin_capture(self):
        if self.recording:
            self.cancel_capture()
            return
        self.recording = True
        self.record_pressed, self.record_keys = set(), []
        self.record_button.setText('Нажмите сочетание…')
        self.record_button.setProperty('recording', True)
        self.record_button.style().polish(self.record_button)
        self.record_button.setFocus()
        self.app.begin_capture(self.capture_event)
        self.escape.setEnabled(False)
        self.capture_hint.setText('Отпустите все клавиши, чтобы сохранить. Esc — отмена.')
        self.capture_timer.start(10000)

    def capture_event(self, event):
        if not self.recording:
            return
        name = key_name(event.name)
        if name == 'esc':
            self.cancel_capture()
            return
        if event.event_type == 'down':
            self.record_pressed.add(name)
            if name not in self.record_keys:
                self.record_keys.append(name)
        else:
            self.record_pressed.discard(name)
            if not self.record_pressed and self.record_keys:
                keys = sorted(self.record_keys, key=lambda key: 0 if any(key.endswith(m) for m in MODIFIERS) else 1)
                self.cancel_capture()
                try:
                    self.app.set_hotkey(normalize_hotkey('+'.join(keys)))
                    self.record_button.setText(self.pretty_hotkey(self.app.settings.hotkey))
                    self.feedback('')
                except (ValueError, OSError) as exc:
                    self.feedback(str(exc), True)

    def cancel_capture(self):
        if self.recording:
            self.recording = False
            self.app.end_capture()
            self.record_button.setText(self.pretty_hotkey(self.app.settings.hotkey))
            self.record_button.setProperty('recording', False)
            self.record_button.style().polish(self.record_button)
            self.capture_hint.setText('Нажмите на сочетание, затем нажмите и отпустите нужные клавиши.')
            QTimer.singleShot(150, lambda: self.escape.setEnabled(True))
        if self.capture_timer is not None:
            self.capture_timer.stop()

    @staticmethod
    def pretty_hotkey(spec):
        return ' + '.join(part.strip().title() for part in spec.split('+'))

    def reset_hotkey(self):
        self.cancel_capture()
        self.guarded(lambda: self.app.set_hotkey(DEFAULTS['hotkey']))
        self.record_button.setText(self.pretty_hotkey(self.app.settings.hotkey))

    def refresh_inputs(self):
        self.mic_combo.set_refreshing(True)
        self.app.commands.put('refresh_devices')

    def catalog_updated(self, preserve_selection=True):
        if self.window is None:
            return
        previous = self.mic_combo.currentData() if preserve_selection else None
        self.mic_options = list(self.app.input_catalog)
        target = self.app.settings.target
        offline = None
        if target and not any(device_id.casefold()==target.endpoint_id.casefold() for device_id,_ in self.mic_options):
            offline = (target.endpoint_id, target.name)
            self.mic_options.append(offline)
        self.mic_combo.clear()
        names = [name for _, name in self.mic_options]
        for device_id, name in self.mic_options:
            title = name if names.count(name) == 1 else name+' ['+device_id+']'
            if (device_id,name)==offline:
                title += ' · не подключён'
            self.mic_combo.addItem(title, (device_id, name))
            if (device_id,name)==offline:
                item = self.mic_combo.model().item(self.mic_combo.count()-1)
                item.setEnabled(False)
        selected = previous if previous in self.mic_options else next(
            (device for device in self.mic_options if target and device[0].casefold() == target.endpoint_id.casefold()), None)
        self.mic_combo.setCurrentIndex(self.mic_options.index(selected) if selected else -1)
        self.mic_combo.setPlaceholderText('Выберите доступный микрофон')
        self.mic_combo.set_refreshing(False)
        if self.mic_combo.popup and self.mic_combo.popup.isVisible():
            self.mic_combo.popup.view.setCurrentIndex(self.mic_combo.model().index(self.mic_combo.currentIndex(),0))
            self.mic_combo.popup.position()

    def select_microphone(self):
        device = self.mic_combo.currentData()
        if not device:
            self.feedback('Выберите устройство из списка', True)
            return
        self.configure_microphone(device)

    def configure_microphone(self, device):
        if self.mic_busy:
            return
        self.mic_busy = True
        # Disabling a focused selector would otherwise move focus to a control
        # below the fold and make QScrollArea jump during automatic saving.
        self.window.setFocus(Qt.FocusReason.OtherFocusReason)
        self.mic_combo.setEnabled(False)
        self.strip_combo.setEnabled(False)
        self.app.commands.put({'kind': 'microphone', 'strip': self.strip_combo.currentData(), 'device': device, 'fallback':True})
        self.feedback('Читаю вход Banana…' if device is None else 'Назначаю микрофон…')

    def mic_result(self, success, message):
        if self.window:
            self.mic_busy = False
            self.mic_combo.setEnabled(True)
            self.strip_combo.setEnabled(True)
            self.strip_combo.setCurrentIndex(self.strip_combo.findData(self.app.settings.strip_index))
            self.feedback(message, not success)
            self.catalog_updated(False)

    def save_buses(self):
        self.guarded(lambda: self.app.change_setting('output_bus_indices',
            [i for i, toggle in enumerate(self.bus_switches) if toggle.isChecked()]))
        for i, toggle in enumerate(self.bus_switches):
            toggle.sync(i in self.app.settings.output_bus_indices)

    def refresh_switches(self):
        for control,enabled_key,volume_key in self.sound_levels:
            if not control.slider.isSliderDown():
                control.sync(getattr(self.app.settings,volume_key),getattr(self.app.settings,enabled_key))
        for toggle, getter in self.switches:
            toggle.sync(getter())

    def refresh(self):
        if not self.window or not self.window.isVisible() or self.window.isMinimized():
            return
        app = self.app
        mic = ('Нет связи', AMBER) if app.state is None else ('Выключен', RED) if app.state else ('Включён', GREEN)
        recovery = app.recovery
        if app.state is None:
            connection = ('Нет VoiceMeeter', RED)
        elif recovery is None or recovery.was_present is None:
            connection = ('Проверка подключения', AMBER)
        elif not recovery.was_present:
            connection = ('Устройство отключено', RED)
        elif recovery.pending:
            connection = ('Восстановление' if app.settings.auto_repair or recovery.manual_requested else 'Требует внимания', AMBER)
        else:
            connection = ('Подключён', GREEN)
        self.mute_status.set_status(*mic)
        self.connection_status.set_status(*connection)
        self.sidebar_connection.set_status('Banana подключена' if app.state is not None else 'Ожидание Banana', GREEN if app.state is not None else AMBER)
        self.logo.setEnabled(app.state is not None)
        self.logo.set_connection(*connection)
        self.refresh_icon()
        target = app.settings.target
        name = target.name if target else 'Микрофон ещё не выбран'
        texts = [(self.device_label, name+f'  ·  Вход {app.settings.strip_index+1}'),
                 (self.banana_label, app.banana_status)]
        texts.extend((widget, app.output_assignments.get(i, '') or 'Не назначен') for i, widget in enumerate(self.output_labels))
        for widget, text in texts:
            if widget.text() != text:
                widget.setText(text)
        self.refresh_switches()
