"""GUI regression tests without hardware access or a visible desktop window."""
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from config import Settings, Target
from quiet_notice import QuietNotice
from settings_window import SettingsWindow
from ui_theme import FileButton, APP_VERSION


class QtUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication(['tests', '-platform', 'offscreen'])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.folder.name)/'settings.json')
        self.stop = threading.Event()
        self.notice = QuietNotice(self.settings, self.stop)
        self.app = SimpleNamespace(settings=self.settings, notice=self.notice, commands=queue.SimpleQueue(),
            enabled=False, state=False, input_catalog=(('id-a', 'Microphone A'), ('id-b', 'Microphone B')),
            output_assignments={}, recovery=None, recovery_status='Checking', banana_status='Ready',
            exit=Mock(), open_log=Mock(), open_banana=Mock(), request_toggle=Mock(), begin_capture=Mock(), end_capture=Mock())
        self.app.change_setting = lambda key, value: self.settings.update(**{key:value})
        self.app.toggle_hotkey = Mock()
        self.app.set_hotkey = lambda value:self.settings.update(hotkey=value)
        self.manager = SettingsWindow(self.app, lambda:False, Mock(), Image.new('RGBA',(64,64)))
        self.manager.show()
        self.qt.processEvents()

    def tearDown(self):
        self.manager.cancel_capture()
        self.manager.timer.stop()
        self.manager.window.hide()
        self.notice.dispatcher.stop()
        self.notice.hide()
        self.manager.window.deleteLater()
        self.notice.window.deleteLater()
        self.qt.processEvents()
        self.folder.cleanup()

    def test_close_to_tray_stops_refresh_and_reopen_restarts_it(self):
        self.manager.hide()
        self.assertFalse(self.manager.window.isVisible())
        self.assertFalse(self.manager.timer.isActive())
        self.app.exit.assert_not_called()
        self.manager.show()
        self.assertTrue(self.manager.timer.isActive())

    def test_failed_save_reverts_toggle_and_reports_error(self):
        toggle = next(t for t, getter in self.manager.switches if t.accessibleName() == 'Автовосстановление')
        self.app.change_setting = Mock(side_effect=OSError('Disk full'))
        QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
        self.assertFalse(toggle.isChecked())
        self.assertFalse(self.settings.auto_repair)
        self.assertIn('Disk full', self.manager.feedback_label.text())

    def test_microphone_selection_saves_only_on_user_activation_and_reverts_on_failure(self):
        self.manager.mic_combo.setCurrentIndex(1)
        self.manager.strip_combo.setCurrentIndex(2)
        # Building, refreshing and browsing devices never applies an assignment.
        queued=[]
        while not self.app.commands.empty(): queued.append(self.app.commands.get_nowait())
        self.assertFalse(any(isinstance(command,dict) for command in queued))
        self.manager.mic_combo.activated.emit(1)
        self.assertEqual(self.app.commands.get_nowait(), {'kind':'microphone','strip':2,'device':('id-b','Microphone B'),'fallback':True})
        self.assertFalse(self.manager.mic_combo.isEnabled())
        self.manager.mic_result(False,'Device lost')
        self.assertTrue(self.manager.mic_combo.isEnabled())
        self.assertEqual(self.manager.strip_combo.currentData(),self.settings.strip_index)
        self.assertIn('Device lost',self.manager.feedback_label.text())

    def test_device_list_refresh_preserves_selection_and_disambiguates_names(self):
        self.manager.mic_combo.setCurrentIndex(1)
        self.app.input_catalog=(('id-b','Microphone B'),('long-id-c','Same name'),('long-id-d','Same name'))
        self.manager.catalog_updated()
        self.assertEqual(self.manager.mic_combo.currentData(), ('id-b','Microphone B'))
        self.assertNotEqual(self.manager.mic_combo.itemText(1), self.manager.mic_combo.itemText(2))
        self.app.input_catalog=()
        self.manager.catalog_updated()
        self.assertEqual(self.manager.mic_combo.currentIndex(),-1)

    def test_recording_saves_only_after_all_keys_released_and_can_reset(self):
        previous=self.settings.hotkey
        self.manager.begin_capture()
        for key in ('ctrl','f9'):
            self.manager.capture_event(SimpleNamespace(name=key,event_type='down'))
        self.manager.capture_event(SimpleNamespace(name='f9',event_type='up'))
        self.assertEqual(self.settings.hotkey,previous)
        self.manager.capture_event(SimpleNamespace(name='ctrl',event_type='up'))
        self.assertEqual(self.settings.hotkey,'ctrl+f9')
        self.assertEqual(self.manager.record_button.text(),'Ctrl + F9')
        self.assertFalse(self.manager.capture_timer.isActive())
        self.manager.reset_hotkey()
        self.assertEqual(self.settings.hotkey,previous)
        self.manager.begin_capture()
        self.manager.hide()
        self.assertFalse(self.manager.recording)
        self.app.end_capture.assert_called()

    def test_numbers_save_automatically_and_revert_on_failure(self):
        self.manager.mic_delay.setValue(2.5)
        self.assertEqual(Settings(self.settings.path).recovery_settle_seconds,2.5)
        self.app.change_setting=Mock(side_effect=OSError('Disk full'))
        self.manager.mic_delay.setValue(3.5)
        self.assertEqual(self.manager.mic_delay.value(),2.5)
        self.assertIn('Disk full',self.manager.feedback_label.text())

    def test_sound_mute_preserves_volume_and_categories_are_independent(self):
        self.manager.navigate(2)
        control=self.manager.mute_level
        control.slider.setValue(37)
        self.assertEqual(self.settings.mute_volume,37)
        before_notice=self.settings.notification_volume
        QTest.mouseClick(control.mute,Qt.MouseButton.LeftButton)
        self.assertFalse(self.settings.mute_sound)
        self.assertEqual(control.slider.value(),37)
        QTest.mouseClick(control.mute,Qt.MouseButton.LeftButton)
        self.assertTrue(self.settings.mute_sound)
        self.assertEqual(self.settings.mute_volume,37)
        self.assertEqual(self.settings.notification_volume,before_notice)
        self.assertEqual(Settings(self.settings.path).mute_volume,37)

    def test_sound_slider_drag_survives_refresh_and_saves_at_release(self):
        control=self.manager.mute_level
        previous=self.settings.mute_volume
        control.slider.setSliderDown(True)
        control.slider.setValue(24)
        self.manager.refresh()
        self.assertEqual(control.slider.value(),24)
        self.assertEqual(self.settings.mute_volume,previous)
        control.slider.setSliderDown(False)
        self.assertEqual(self.settings.mute_volume,24)

    def test_sound_preview_uses_category_volume_and_respects_mute(self):
        self.settings.update(mute_volume=35,notification_volume=55,notification_sound=True)
        with patch('settings_window.sounds.play') as play:
            self.manager.preview_sound('sound_on_file','unmute','mute_sound','mute_volume')
            play.assert_called_once_with('','unmute',35)
            play.reset_mock()
            self.settings.update(mute_sound=False)
            self.manager.preview_sound('sound_on_file','unmute','mute_sound','mute_volume')
            play.assert_not_called()
            self.manager.preview_sound('notification_sound_file','notice','notification_sound','notification_volume')
            play.assert_called_once_with('','notice',55)

    def test_setup_preview_switches_visual_state_without_installing_or_detecting_devices(self):
        from installer import SetupWindow
        with patch('installer.detect_banana') as detect,patch('installer.install') as install:
            for present in (True,False):
                window=SetupWindow(Path(self.folder.name),preview=True,banana_present=present)
                window.show();self.qt.processEvents()
                self.assertEqual(window.banana_present,present)
                self.assertFalse(window.button.isEnabled())
                self.assertEqual('и Banana' in window.button.text(),not present)
                self.assertNotIn('<a ',window.credit_label.text())
                self.assertEqual(window.links_label.text().count('<a '),3)
                self.assertGreater(window.links_label.mapTo(window,QPoint()).y(),window.credit_label.mapTo(window,QPoint()).y())
                window.close();window.deleteLater();self.qt.processEvents()
            detect.assert_not_called();install.assert_not_called()

    def test_sidebar_compacts_for_small_windows_and_restores_user_width(self):
        self.manager.window.resize(1100,800)
        self.manager.preferred_sidebar=224
        self.manager.adapt_sidebar()
        self.assertFalse(self.manager.compact)
        self.manager.window.resize(800,640)
        self.qt.processEvents()
        self.assertTrue(self.manager.compact)
        self.assertFalse(self.manager.brand.isVisible())
        self.manager.window.resize(1100,800)
        self.qt.processEvents()
        self.assertFalse(self.manager.compact)
        self.assertAlmostEqual(self.manager.splitter.sizes()[0],224,delta=2)

    def test_sidebar_snaps_during_drag_and_focus_loss_cannot_leave_arbitrary_width(self):
        self.manager.window.resize(1100,800)
        self.manager.sidebar_dragged(10000)
        self.assertEqual(self.manager.splitter.sizes()[0],224)
        self.manager.sidebar_dragged(110)
        self.assertEqual(self.manager.splitter.sizes()[0],76)
        self.assertEqual(self.settings.sidebar_width,76)
        self.manager.splitter.handle(1).dragging=True
        self.manager.splitter.cancel_drag()
        self.manager.adapt_sidebar()
        self.assertEqual(self.manager.splitter.sizes()[0],76)
        self.assertFalse(self.manager.splitter.handle(1).dragging)
        self.manager.sidebar_dragged(190)
        self.assertEqual(self.manager.splitter.sizes()[0],224)

    def test_header_label_is_single_line_and_connection_dot_is_independent_of_mute(self):
        self.assertFalse(self.manager.brand.wordWrap())
        self.assertEqual(self.manager.brand.text(),'Sound Control')
        self.app.recovery=SimpleNamespace(was_present=True,pending=False)
        self.app.state=True
        self.manager.refresh()
        self.assertEqual(self.manager.logo.connection[0],'Подключён')
        self.app.recovery.was_present=False
        self.manager.refresh()
        self.assertEqual(self.manager.logo.connection[0],'Устройство отключено')

    def test_changing_strip_learns_its_existing_assignment_automatically(self):
        while not self.app.commands.empty():self.app.commands.get_nowait()
        self.manager.strip_combo.setCurrentIndex(2)
        self.manager.strip_combo.activated.emit(2)
        self.assertEqual(self.app.commands.get_nowait(),{'kind':'microphone','strip':2,'device':None,'fallback':True})

    def test_logo_requests_toggle_without_changing_hotkey_setting(self):
        QTest.mouseClick(self.manager.logo,Qt.MouseButton.LeftButton)
        self.app.request_toggle.assert_called_once()
        self.assertFalse(self.settings.hotkey_enabled)

    def test_file_paths_are_buttons_that_choose_and_save_without_an_edit_field(self):
        self.manager.navigate(3)
        picker=next(button for button in self.manager.window.findChildren(FileButton)
                    if button.placeholder=='Определять автоматически')
        with patch('settings_window.QFileDialog.getOpenFileName',return_value=('C:/Audio/banana.exe','')) as choose:
            picker.click()
        choose.assert_called_once()
        self.assertEqual(self.settings.banana_exe,'C:/Audio/banana.exe')
        self.assertEqual(picker.path,'C:/Audio/banana.exe')
        with patch('settings_window.QFileDialog.getOpenFileName',return_value=('','')):
            picker.click()
        self.assertEqual(self.settings.banana_exe,'C:/Audio/banana.exe')

    def test_navigation_and_logo_do_not_shift_horizontally_when_collapsed(self):
        self.manager.window.resize(1100,800)
        self.manager.preferred_sidebar=224;self.manager.adapt_sidebar();self.qt.processEvents()
        positions=[button.mapTo(self.manager.sidebar,QPoint(0,0)).x()+button.icon_rect().x()
                   for button in self.manager.nav_buttons+[self.manager.journal]]
        logo_x=self.manager.logo.mapTo(self.manager.sidebar,QPoint(0,0)).x()
        self.manager.sidebar_dragged(100);self.qt.processEvents()
        self.assertEqual(positions,[button.mapTo(self.manager.sidebar,QPoint(0,0)).x()+button.icon_rect().x()
                        for button in self.manager.nav_buttons+[self.manager.journal]])
        self.assertEqual(logo_x,self.manager.logo.mapTo(self.manager.sidebar,QPoint(0,0)).x())
        self.assertTrue(self.manager.version_label.isVisible())
        self.assertIn(APP_VERSION.split('-')[0],self.manager.version_label.text())
        self.manager.sidebar_dragged(200);self.qt.processEvents()
        self.assertEqual(self.manager.version_label.text(),'v'+APP_VERSION)

    def test_journal_mouse_focus_does_not_become_a_second_selected_page(self):
        self.manager.navigate(3)
        self.manager.journal.setFocus(Qt.FocusReason.MouseFocusReason)
        self.qt.processEvents()
        self.assertTrue(self.manager.nav_buttons[3].isChecked())
        self.assertFalse(self.manager.journal.isChecked())
        self.assertFalse(self.manager.journal.keyboard_focus)
        self.assertEqual(sum(button.isChecked() for button in self.manager.nav_buttons),1)

    def test_sidebar_handle_has_no_obscuring_tooltip(self):
        self.assertEqual(self.manager.splitter.handle(1).toolTip(),'')

    def test_version_keeps_the_same_left_anchor_in_both_sidebar_sizes(self):
        positions=[]
        for width in (224,76,224):
            self.manager.sidebar_dragged(width)
            self.qt.processEvents()
            label=self.manager.version_label
            positions.append(label.mapTo(self.manager.sidebar,QPoint()).x()+label.contentsMargins().left())
        self.assertEqual(positions,[positions[0]]*3)

    def test_title_bar_double_click_maximizes_and_restores(self):
        bar=self.manager.window.title_bar
        before=self.manager.window.geometry()
        QTest.mouseDClick(bar,Qt.MouseButton.LeftButton,pos=QPoint(200,24))
        self.assertTrue(self.manager.window.isMaximized())
        QTest.mouseDClick(bar,Qt.MouseButton.LeftButton,pos=QPoint(200,24))
        self.assertFalse(self.manager.window.isMaximized())
        self.assertEqual(self.manager.window.geometry(),before)

    def test_horizontal_stretch_preserves_height_and_can_restore_width(self):
        window=self.manager.window
        window.setGeometry(100,110,500,420)
        before=window.geometry()
        window.frame.stretch_horizontal()
        after=window.geometry()
        self.assertEqual(after.width(),window.screen().availableGeometry().width())
        self.assertEqual((after.y(),after.height()),(before.y(),before.height()))
        window.frame.stretch_horizontal()
        self.assertEqual(window.geometry(),before)

    def test_horizontal_stretch_maximize_restores_the_original_normal_geometry(self):
        window=self.manager.window
        window.setGeometry(100,110,500,420)
        before=window.geometry()
        window.frame.stretch_horizontal()
        window.title_bar.maximize()
        self.assertTrue(window.isMaximized())
        window.title_bar.maximize()
        self.assertEqual(window.geometry(),before)

    def test_horizontal_stretch_drag_restore_preserves_pointer_anchor_and_height(self):
        window=self.manager.window
        window.setGeometry(100,110,500,420)
        before=window.geometry()
        window.frame.stretch_horizontal()
        wide=window.geometry()
        pointer=wide.left()+wide.width()//3
        window.frame.restore_horizontal(pointer)
        self.assertEqual(window.width(),500)
        self.assertEqual((window.geometry().y(),window.height()),(before.y(),before.height()))
        self.assertAlmostEqual(pointer-window.geometry().x(),500/3,delta=1)

    def test_tray_menu_actions_and_current_hotkey_state(self):
        from tray_menu import TrayMenu,TrayController,TRAY_TITLE
        self.app.show_settings=Mock()
        menu=TrayMenu(self.app)
        self.app.enabled=True
        menu.update_state()
        self.assertTrue(menu.hotkey.isChecked())
        self.assertEqual(menu.hotkey.detail,self.settings.hotkey.title())
        menu.actions[3].click()
        self.app.show_settings.assert_called_once()
        while not self.app.commands.empty():self.app.commands.get_nowait()
        menu.actions[1].click()
        self.assertEqual(self.app.commands.get_nowait(),'repair')
        menu.actions[4].click()
        self.app.exit.assert_called_once()
        self.assertIn(APP_VERSION,TRAY_TITLE)
        menu.deleteLater()

    def test_tray_mouse_focus_does_not_persist_as_a_second_highlight(self):
        from tray_menu import TrayMenu
        self.app.show_settings=Mock()
        menu=TrayMenu(self.app)
        menu.show()
        menu.hotkey.setFocus(Qt.FocusReason.MouseFocusReason)
        self.qt.processEvents()
        self.assertFalse(menu.hotkey.keyboard_focus)
        menu.hotkey.clearFocus()
        menu.hotkey.setFocus(Qt.FocusReason.TabFocusReason)
        self.assertTrue(menu.hotkey.keyboard_focus)
        menu.hide()
        self.assertFalse(menu.hotkey.keyboard_focus)
        self.assertFalse(menu.hotkey.isDown())
        menu.deleteLater()

    def test_navigation_returns_each_page_to_the_top(self):
        self.manager.window.resize(720,580)
        for index in range(4):
            self.manager.navigate(index)
            self.qt.processEvents()
            bar=self.manager.stack.currentWidget().verticalScrollBar()
            self.assertGreater(bar.maximum(),0)
            bar.setValue(bar.maximum())
            self.manager.navigate((index+1)%4)
            self.manager.navigate(index)
            self.qt.processEvents()
            self.assertEqual(bar.value(),0,index)

    def test_connection_indicator_opens_mixer_without_changing_audio_settings(self):
        self.settings.save()
        previous=self.settings.path.read_bytes()
        for compact in (False,True):
            self.manager.update_sidebar(compact)
            QTest.mouseClick(self.manager.sidebar_connection,Qt.MouseButton.LeftButton)
        self.assertEqual(self.app.open_banana.call_count,2)
        self.assertEqual(self.settings.path.read_bytes(),previous)
        self.app.request_toggle.assert_not_called()

    def test_wheel_over_number_scrolls_page_without_focusing_or_changing_value(self):
        self.manager.window.resize(720,580)
        self.manager.navigate(0)
        self.qt.processEvents()
        field=self.manager.mic_delay
        scroll=self.manager.stack.currentWidget()
        scroll.ensureWidgetVisible(field)
        self.manager.nav_buttons[0].setFocus()
        self.qt.processEvents()
        before=field.value()
        position=QPointF(field.rect().center())
        event=QWheelEvent(position,QPointF(field.mapToGlobal(position.toPoint())),
            QPoint(),QPoint(0,-120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,False)
        self.qt.sendEvent(field,event)
        self.qt.processEvents()
        self.assertEqual(field.value(),before)
        self.assertFalse(field.hasFocus())
        self.assertFalse(field.lineEdit().hasFocus())
        # Clicking the field must still enable normal keyboard editing.
        QTest.mouseClick(field.lineEdit(),Qt.MouseButton.LeftButton)
        self.assertTrue(field.hasFocus() or field.lineEdit().hasFocus())
        self.qt.sendEvent(field,event)
        self.assertFalse(field.hasFocus() or field.lineEdit().hasFocus())
        self.assertEqual(field.value(),before)

    def test_mute_updates_shared_sidebar_and_taskbar_icon_including_slash(self):
        self.app.recovery=SimpleNamespace(was_present=True,pending=False)
        self.manager.refresh()
        unmuted=self.manager.window.windowIcon().cacheKey()
        self.app.state=True
        self.manager.refresh()
        icon=self.manager.window.windowIcon()
        self.assertNotEqual(icon.cacheKey(),unmuted)
        self.assertEqual(icon.cacheKey(),self.manager.logo.icon().cacheKey())
        image=icon.pixmap(192,192).toImage()
        pixel=image.pixelColor(55,136)  # Reverse diagonal, colour-matched muted slash.
        self.assertGreater(pixel.alpha(),180)
        self.assertGreater(pixel.red(),pixel.green())
        self.assertLess(image.pixelColor(144,144).alpha(),30)  # No slash near the connection dot.
        capsule=image.pixelColor(96,48)
        self.assertGreater(capsule.red(),capsule.green())

    def test_dropdown_requests_refresh_on_open_without_applying_device(self):
        while not self.app.commands.empty():self.app.commands.get_nowait()
        self.manager.mic_combo.showPopup()
        self.assertEqual(self.app.commands.get_nowait(),'refresh_devices')
        self.assertTrue(self.manager.mic_combo.refreshing)
        self.assertFalse(self.manager.mic_combo.popup.view.isEnabled())
        self.manager.catalog_updated()
        self.assertTrue(self.manager.mic_combo.popup.view.isEnabled())
        self.manager.mic_combo.hidePopup()

    def test_saved_offline_device_remains_selected_instead_of_silent_fallback(self):
        self.settings.target=Target('Offline USB microphone','wdm','offline-id')
        self.manager.mic_combo.setCurrentIndex(-1)
        self.manager.catalog_updated()
        self.assertEqual(self.manager.mic_combo.currentData(),('offline-id','Offline USB microphone'))
        self.assertIn('не подключён',self.manager.mic_combo.currentText())

    def test_warning_persists_restored_notice_expires_without_activation(self):
        self.settings.update(notice_seconds=0)
        self.notice.show('Микрофон отключён','Проверьте кабель')
        self.assertFalse(self.notice.hide_timer.isActive())
        self.assertTrue(self.notice.window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertTrue(self.notice.window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.notice.show('Микрофон снова подключён','Восстановлено')
        self.assertEqual(self.notice.hide_timer.interval(),5000)
        self.assertTrue(self.notice.hide_timer.isActive())

    def test_window_only_close_exits(self):
        self.settings.update(ui_mode='window')
        self.manager.window.close()
        self.app.exit.assert_called_once()

    def test_all_sections_remain_scrollable_at_minimum_size(self):
        for width in (1080,900,720,480):
            self.manager.window.resize(width,420)
            for index in range(4):
                self.manager.navigate(index)
                self.qt.processEvents()
                scroll=self.manager.stack.currentWidget()
                self.assertGreaterEqual(scroll.widget().width(),scroll.viewport().width())
                self.assertEqual(scroll.horizontalScrollBar().maximum(),0, (width,index))
        # Native material failures must retain a fully opaque readable surface.
        with patch('ui_theme.native_backdrop',return_value=False):
            self.manager.window.hide()
            self.manager.window.show()
            self.assertFalse(self.manager.window.backdrop)


if __name__ == '__main__':
    unittest.main()
