"""Documentation UI with synthetic devices. Never loads the audio SDK/profile."""
import argparse
from pathlib import Path
import queue
import sys
import tempfile
import threading
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import QTimer
from config import Settings,Target
from quiet_notice import QuietNotice
from settings_window import SettingsWindow
from installer import SetupWindow
from tray_menu import TrayMenu
from app_icons import microphone_image

parser=argparse.ArgumentParser()
parser.add_argument('--setup',choices=('installed','missing'))
parser.add_argument('--tray',action='store_true')
parser.add_argument('--notice',action='store_true')
parser.add_argument('--compact',action='store_true')
args=parser.parse_args()
profile=tempfile.TemporaryDirectory(prefix='SoundControl-docs-')
settings=Settings(Path(profile.name)/'settings.json')
settings.update(hotkey_enabled=True,auto_repair=True,launch_banana=True,hide_banana_tray=True,
    output_reconnect=True,output_device_scope='all',output_bus_indices=[0,1],notification_sound=True,
    sidebar_width=76 if args.compact else 224,notice_seconds=0)
settings.target=Target('USB Microphone','wdm','demo-input-a')
stop=threading.Event();notice=QuietNotice(settings,stop)
app=SimpleNamespace(settings=settings,notice=notice,commands=queue.SimpleQueue(),state=False,
    enabled=True,input_catalog=(('demo-input-a','USB Microphone'),('demo-input-b','Headset Microphone')),
    recovery=SimpleNamespace(was_present=True,pending=False),recovery_status='Микрофон подключён',
    banana_status='Banana работает в фоне',
    output_assignments={0:'Desktop Headphones',1:'Bluetooth Speaker',2:'HDMI Monitor'})
app.exit=stop.set
app.request_toggle=lambda:(setattr(app,'state',not app.state),manager.refresh())
app.change_setting=lambda key,value:settings.update(**{key:value})
app.toggle_hotkey=lambda:(settings.update(hotkey_enabled=not app.enabled),setattr(app,'enabled',settings.hotkey_enabled))
app.set_hotkey=lambda value:settings.update(hotkey=value)
app.open_banana=lambda:None;app.open_log=lambda:None
app.begin_capture=lambda callback:None;app.end_capture=lambda:None
manager=SettingsWindow(app,lambda:True,lambda:None,microphone_image(False))
app.show_settings=manager.show
if args.setup:
    window=SetupWindow(ROOT,preview=True,banana_present=args.setup=='installed')
    window.path.set_path(r'C:\Apps\SoundControl')
    window.frame.configure();window.show()
elif args.tray:
    window=TrayMenu(app);window.open()
elif args.notice:
    notice.show('Микрофон отключён','Проверьте подключение устройства. После возвращения микрофона Sound Control попробует восстановить выбранный вход.')
    window=notice.window
else:
    manager.show();window=manager.window
command=ROOT/'.qa/docs-command.txt';command.parent.mkdir(exist_ok=True)
def poll():
    if command.exists():
        text=command.read_text().strip();command.unlink()
        if text=='quit':stop.set()
        elif text.startswith('page:') and manager.window:manager.navigate(int(text[-1]))
timer=QTimer();timer.timeout.connect(poll);timer.start(150)
print('Documentation preview ready',flush=True)
notice.run()
profile.cleanup()
