import ctypes
from pathlib import Path
import queue
import re
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from config import Settings, Target
from hotkeys import HotkeyBinding
from settings_window import SettingsWindow
from voicemeeter_right_ctrl_mute import Application, Remote
from bluetooth_outputs import BluetoothOutputs, OutputEndpoint
from quiet_notice import QuietNotice
from comtypes import COMError


class DLL:
    def __init__(self):
        self.names={0:'Mic A',1:'Mic A',2:'Mic B'}
        self.mutes={0:False,1:True,2:False}
        self.drivers={0:3,1:3,2:3}
        self.writes=[]
        self.reject=False
    def VBVMR_Login(self):return 0
    def VBVMR_Logout(self):return 0
    def VBVMR_IsParametersDirty(self):return 1
    def VBVMR_GetParameterFloat(self,param,ptr):
        text=param.decode(); index=int(re.search(r'\[(\d)\]',text)[1])
        value=self.mutes[index] if text.endswith('.Mute') else self.drivers[index] if text.endswith('.type') else 48000
        ctypes.cast(ptr,ctypes.POINTER(ctypes.c_float))[0]=float(value)
        return 0
    def VBVMR_SetParameterFloat(self,param,value):
        self.writes.append((param,value.value))
        index=int(re.search(rb'\[(\d)\]',param)[1]);self.mutes[index]=bool(value.value)
        return 0
    def VBVMR_GetParameterStringW(self,param,buffer):
        index=int(re.search(rb'\[(\d)\]',param)[1])
        buffer.value=self.names[index] if param.startswith(b'Strip') else 'Output '+str(index)
        return 0
    def VBVMR_SetParameterStringW(self,param,name):
        self.writes.append((param,name))
        if self.reject:return -2
        index=int(re.search(rb'\[(\d)\]',param)[1]);self.names[index]=name;self.drivers[index]=3;return 0


class Endpoints:
    def capture_devices(self):return (('a','Mic A'),('b','Mic B'))
    def find(self,name):return next((device_id for device_id,n in self.capture_devices() if n==name),None)
    find_configured=find
    def default_capture(self):return ('b','Mic B')


class GeneralSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.settings=Settings(Path(self.temp.name)/'settings.json')
        self.dll=DLL();self.remote=Remote(self.dll)

    def test_mute_targets_selected_strip_only(self):
        self.remote.strip_index=2
        self.assertTrue(self.remote.toggle())
        self.assertEqual(self.dll.writes[-1][0],b'Strip[2].Mute')
        self.assertFalse(self.dll.mutes[0]);self.assertTrue(self.dll.mutes[1])

    def test_output_names_support_a1_a2_a3_selection(self):
        self.assertEqual(self.remote.output_names([0,2]),{'Output 0','Output 2'})
        self.assertEqual(self.remote.output_names([]),set())

    def app(self):
        app=Application.__new__(Application)
        app.settings=self.settings;app.remote=self.remote;app.recovery=None
        app.requests=queue.Queue(maxsize=1)
        app.state=False;app.icon=Mock();app.notice=SimpleNamespace(post=Mock(),call=Mock());app.preferences=Mock()
        return app

    def test_initial_microphone_prefers_banana_and_preserves_driver_without_writes(self):
        app=self.app();self.remote.read();self.dll.drivers[0]=1
        self.assertTrue(app.initialize_microphone(Endpoints(),False))
        self.assertEqual(self.settings.target,Target('Mic A','mme','a'))
        self.assertEqual(self.dll.writes,[])

    def test_initial_microphone_falls_back_to_windows_only_without_banana_device(self):
        app=self.app();self.dll.names[0]='';self.remote.read()
        self.assertTrue(app.initialize_microphone(Endpoints(),False))
        self.assertEqual(self.settings.target,Target('Mic B','wdm','b'))
        self.assertEqual(self.dll.names[0],'Mic B')

    def test_initial_microphone_never_replaces_existing_saved_choice(self):
        self.settings.target=Target('Saved offline microphone','ks','offline-id')
        app=self.app()
        self.assertFalse(app.initialize_microphone(Endpoints(),False))
        self.assertEqual(self.settings.target.endpoint_id,'offline-id')
        self.assertEqual(self.dll.writes,[])

    def test_auto_strip_change_uses_its_assignment_without_changing_driver(self):
        app=self.app();self.dll.drivers[1]=1
        ok,_=app.configure_microphone({'strip':1,'device':None,'fallback':True},Endpoints(),False)
        self.assertTrue(ok)
        self.assertEqual(self.settings.target,Target('Mic A','mme','a'))
        self.assertEqual(self.dll.writes,[])

    def test_auto_empty_strip_uses_default_capture(self):
        app=self.app();self.dll.names[2]=''
        ok,_=app.configure_microphone({'strip':2,'device':None,'fallback':True},Endpoints(),False)
        self.assertTrue(ok)
        self.assertEqual(self.settings.target,Target('Mic B','wdm','b'))
        self.assertEqual(self.dll.names[2],'Mic B')

    def test_device_selection_assigns_and_persists_selected_input(self):
        app=self.app()
        app.requests.put(True)
        ok,message=app.configure_microphone({'strip':2,'device':('b','Mic B')},Endpoints(),False)
        self.assertTrue(ok,message)
        saved=Settings(self.settings.path)
        self.assertEqual(saved.strip_index,2);self.assertEqual(saved.target,Target('Mic B','wdm','b'))
        self.assertEqual(self.dll.names[0],'Mic A')
        self.assertTrue(app.requests.empty())

    def test_device_selection_rejection_keeps_previous_target(self):
        self.settings.target=Target('Mic A','wdm','a')
        self.dll.reject=True;app=self.app()
        ok,_=app.configure_microphone({'strip':1,'device':('b','Mic B')},Endpoints(),False)
        self.assertFalse(ok);self.assertEqual(self.settings.strip_index,0)
        self.assertEqual(self.settings.target.name,'Mic A');self.assertEqual(self.remote.strip_index,0)

    def test_explicit_windows_selection_uses_wdm_even_for_same_name(self):
        self.dll.drivers[0]=1
        app=self.app()
        ok,message=app.configure_microphone({'strip':0,'device':('a','Mic A')},Endpoints(),False)
        self.assertTrue(ok,message)
        self.assertIn((b'Strip[0].device.wdm','Mic A'),self.dll.writes)
        self.assertEqual(self.dll.drivers[0],3)

    def test_capture_does_not_mute_and_reset_restores_hotkey(self):
        app=Application.__new__(Application);app.binding=HotkeyBinding('f8')
        app.key_lock=threading.Lock();app.enabled=True;app.state=False
        app.suspended=threading.Event()
        app.requests=queue.Queue(maxsize=1);recorded=[];app.capture_callback=recorded.append
        app.notice=SimpleNamespace(call=lambda callback:callback())
        app.on_key(SimpleNamespace(name='f8',event_type='down'))
        self.assertTrue(app.requests.empty());self.assertEqual(len(recorded),1)
        app.end_capture()
        app.on_key(SimpleNamespace(name='f8',event_type='down'))
        self.assertFalse(app.requests.empty())

    def test_device_disappearing_during_selection_is_handled(self):
        app=self.app();endpoints=Endpoints()
        endpoints.capture_devices=Mock(side_effect=COMError(-1,'device disappeared',None))
        ok,_=app.configure_microphone({'strip':1,'device':('b','Mic B')},endpoints,False)
        self.assertFalse(ok);self.assertEqual(self.remote.strip_index,0)

    def test_window_only_close_exits_instead_of_hiding_last_window(self):
        manager=SettingsWindow.__new__(SettingsWindow)
        manager.cancel_capture=Mock();manager.window=Mock();manager.app=Mock()
        manager.app.settings.ui_mode='window';manager.app.settings.close_to_tray=True
        manager.hide();manager.app.exit.assert_called_once();manager.window.hide.assert_not_called()

    def test_all_device_scope_can_handle_usb_and_custom_delays(self):
        remote=SimpleNamespace(restart_engine=Mock(return_value=True))
        monitor=BluetoothOutputs(remote)
        off=[OutputEndpoint('usb','USB output',False,False)]
        on=[OutputEndpoint('usb','USB output',True,False)]
        monitor.tick(off,[],{'USB output'},True,True,now=0,settle_seconds=4,cooldown_seconds=10)
        monitor.tick(on,[],{'USB output'},True,True,now=1,settle_seconds=4,cooldown_seconds=10)
        monitor.tick(on,[],{'USB output'},True,True,now=4,settle_seconds=4,cooldown_seconds=10)
        remote.restart_engine.assert_not_called()
        monitor.tick(on,[],{'USB output'},True,True,now=5,settle_seconds=4,cooldown_seconds=10)
        remote.restart_engine.assert_called_once()

    def test_notice_sound_can_play_with_panel_disabled(self):
        notice=QuietNotice.__new__(QuietNotice)
        notice.stop=threading.Event();notice.ui_tasks=queue.SimpleQueue();notice.messages=queue.SimpleQueue()
        notice.root=Mock();notice.hide=Mock();notice.show=Mock()
        notice.visible=True
        notice.settings=SimpleNamespace(notification_sound=True,notification_sound_file='example.wav',notification_volume=42,notifications=False)
        notice.messages.put(('Device disconnected','Check cable'))
        with patch('quiet_notice.sounds.play') as play:
            notice.poll();play.assert_called_once_with('example.wav','notice',42)
        notice.show.assert_not_called();notice.hide.assert_called_once()


if __name__=='__main__':unittest.main()
