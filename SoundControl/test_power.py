"""Resume regressions without sleeping the PC or opening another SDK session."""
import queue
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication
from power_lifecycle import PowerLifecycle
from voicemeeter_right_ctrl_mute import Application, Remote
from test_muter import FakeDLL


class PowerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication(['tests','-platform','offscreen'])

    def test_duplicate_suspend_and_resume_messages_are_coalesced(self):
        callback=Mock()
        listener=PowerLifecycle(callback)
        self.assertFalse(listener.isVisible())
        for kind,now in [('suspend',1),('suspend',2),('resume',100),('resume',101),
                         ('suspend',102),('resume',103),('explorer',104)]:
            listener.notify(kind,now)
        self.assertEqual([c.args[0] for c in callback.call_args_list],
                         ['suspend','resume','suspend','resume','explorer'])
        listener.stop()
        self.assertFalse(listener.timer.isActive())
        listener.deleteLater()

    def test_missed_power_broadcast_is_recovered_from_a_timer_gap(self):
        callback=Mock()
        listener=PowerLifecycle(callback)
        listener.last_tick=100
        with patch('power_lifecycle.time.time',return_value=160):
            listener.check_gap()
            listener.check_gap()
        callback.assert_called_once_with('resume')
        listener.stop();listener.deleteLater()

    def test_resume_restores_ui_and_replaces_only_our_keyboard_callback(self):
        app=Application.__new__(Application)
        app.stop=threading.Event()
        app.suspended=threading.Event();app.suspended.set()
        app.resume_pending=threading.Event()
        app.icon=Mock();app.key_lock=threading.Lock();app.binding=Mock()
        app.preferences=Mock();app.notice=Mock();app.hook='old-hook'
        with patch('voicemeeter_right_ctrl_mute.keyboard.unhook') as unhook, \
             patch('voicemeeter_right_ctrl_mute.keyboard.hook',return_value='new-hook') as hook:
            app.power_changed('resume')
        self.assertFalse(app.suspended.is_set())
        self.assertTrue(app.resume_pending.is_set())
        app.icon.restore.assert_called_once()
        app.binding.reset.assert_called_once()
        unhook.assert_called_once_with('old-hook')
        hook.assert_called_once_with(app.on_key)
        self.assertEqual(app.hook,'new-hook')

    def test_sleep_resume_recreates_endpoint_monitor_but_keeps_one_sdk_session(self):
        app=Application.__new__(Application)
        dll=FakeDLL();dll.online=True
        dll.VBVMR_Login=Mock(wraps=dll.VBVMR_Login)
        dll.VBVMR_Logout=Mock(wraps=dll.VBVMR_Logout)
        app.remote=Remote(dll)
        app.remote.output_devices=Mock(return_value={})
        app.stop=threading.Event();app.suspended=threading.Event();app.resume_pending=threading.Event()
        app.interrupted_repair=False
        app.control=Mock();app.notice=Mock();app.preferences=Mock()
        app.commands=queue.SimpleQueue();app.requests=queue.Queue()
        app.update_app_icon=Mock()
        app.state=None;app.recovery=None;app.host=None;app.last_output_config=None
        app.bluetooth=Mock()
        app.settings=SimpleNamespace(banana_exe='',target=object(),output_bus_indices=[1,2],output_device_scope='bluetooth',
            output_reconnect=False,output_settle_seconds=2,output_cooldown_seconds=15)
        app.icon=Mock()
        count=0
        def control(kind):
            nonlocal count
            if kind!='quit':return False
            count+=1
            if count in (2,4,6):app.suspended.set()
            if count in (3,5,7):app.suspended.clear();app.resume_pending.set()
            return count==8
        app.control.pending.side_effect=control
        endpoints=[Mock() for _ in range(4)]
        for endpoint in endpoints:
            endpoint.capture_devices.return_value=()
            endpoint.output_snapshot.return_value=([],[])
        def recovery(*args):
            return SimpleNamespace(phase='idle',pending=False,status='Ready',tick=Mock())
        with patch('voicemeeter_right_ctrl_mute.find_vm_dll',return_value=Path('test.dll')), \
             patch('voicemeeter_right_ctrl_mute.WindowsBanana'), \
             patch('voicemeeter_right_ctrl_mute.BananaHost'), \
             patch('voicemeeter_right_ctrl_mute.Endpoints',side_effect=endpoints) as factory, \
             patch('voicemeeter_right_ctrl_mute.Recovery',side_effect=recovery), \
             patch('voicemeeter_right_ctrl_mute.BluetoothOutputs'):
            errors=[]
            def run():
                try:app.work_once()
                except BaseException as exc:errors.append(exc)
            worker=threading.Thread(target=run)
            worker.start();worker.join(4)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors,[])
        self.assertEqual(factory.call_count,4)
        for endpoint in endpoints:endpoint.close.assert_called_once()
        dll.VBVMR_Login.assert_called_once()
        dll.VBVMR_Logout.assert_called_once()
        self.assertEqual(app.remote.login_count,1)

    def test_failed_audio_worker_is_retried_instead_of_staying_dead(self):
        app=Application.__new__(Application)
        app.stop=Mock()
        app.stop.is_set.side_effect=[False,False,False,True,True]
        app.work_once=Mock(side_effect=[RuntimeError('transient cleanup failure'),None])
        app.work()
        self.assertEqual(app.work_once.call_count,2)
        app.stop.wait.assert_called_once_with(5)


if __name__=='__main__':unittest.main()
