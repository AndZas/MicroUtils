"""Optional offline pack: own app + unmodified official VB-Audio installer."""
import ctypes
from ctypes import wintypes as W
import hashlib
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import winreg
from PySide6.QtCore import QObject,Signal,Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication,QVBoxLayout,QHBoxLayout,QLabel,QFrame,QFileDialog
from ui_theme import GlassWindow,TitleBar,ActionButton,FileButton,Toggle,application_icon,APP_VERSION,MUTED,GREEN

VENDOR_FILENAME='VoicemeeterSetup_v2130.zip'
VENDOR_URL='https://download.vb-audio.com/Download_CABLE/'+VENDOR_FILENAME
VENDOR_SHA256='ee8b1f6cd4728233c8ae52897128c9af02367c091b88f55a1996d424e04d3965'
VENDOR_SETUP='voicemeeterprosetup.exe'
log=logging.getLogger('VoicemeeterMuteTray')


def detect_banana():
    from voicemeeter_right_ctrl_mute import find_vm_dll
    try:return find_vm_dll().parent
    except FileNotFoundError:return None


def verify_vendor(path):
    if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=VENDOR_SHA256:
        raise ValueError('Архив Banana повреждён или отличается от проверенного пакета VB-Audio')
    with zipfile.ZipFile(path) as archive:
        if archive.namelist()!=[VENDOR_SETUP]:
            raise ValueError('Неожиданный состав архива Banana')


def run_vendor_setup(setup):
    """Elevation belongs to the original signed installer; never elevate our app."""
    class ExecuteInfo(ctypes.Structure):
        _fields_=[('size',W.DWORD),('mask',W.ULONG),('hwnd',W.HWND),('verb',W.LPCWSTR),
                  ('file',W.LPCWSTR),('parameters',W.LPCWSTR),('directory',W.LPCWSTR),
                  ('show',ctypes.c_int),('instance',W.HINSTANCE),('idlist',W.LPVOID),
                  ('class_name',W.LPCWSTR),('class_key',W.HKEY),('hotkey',W.DWORD),
                  ('icon',W.HANDLE),('process',W.HANDLE)]
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    shell.ShellExecuteExW.argtypes=[ctypes.POINTER(ExecuteInfo)]
    shell.ShellExecuteExW.restype=W.BOOL
    kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]
    kernel.GetExitCodeProcess.argtypes=[W.HANDLE,ctypes.POINTER(W.DWORD)]
    kernel.CloseHandle.argtypes=[W.HANDLE]
    info=ExecuteInfo();info.size=ctypes.sizeof(info);info.mask=0x40
    info.verb='runas';info.file=str(setup);info.parameters='-i -h';info.directory=str(setup.parent);info.show=0
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error=ctypes.get_last_error()
        if error==1223:raise RuntimeError('Установка отменена в запросе прав Windows')
        raise ctypes.WinError(error)
    try:
        while kernel.WaitForSingleObject(info.process,500)==258:pass
        code=W.DWORD()
        if not kernel.GetExitCodeProcess(info.process,ctypes.byref(code)):raise ctypes.WinError(ctypes.get_last_error())
        if code.value not in (0,3010):raise RuntimeError(f'Установщик Banana завершился с кодом {code.value}')
    finally:kernel.CloseHandle(info.process)


def stop_existing():
    from voicemeeter_right_ctrl_mute import APP_NAME,acquire_instance
    from local_control import LocalControl
    if not LocalControl.request(APP_NAME,'quit'):return
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        instance=acquire_instance()
        if instance:
            instance[0].CloseHandle(instance[1]);return
        time.sleep(.1)
    raise RuntimeError('Закройте работающий Sound Control и повторите установку')


def set_startup(exe,enabled):
    from voicemeeter_right_ctrl_mute import RUN_KEY,APP_NAME
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
        if enabled:winreg.SetValueEx(key,APP_NAME,0,winreg.REG_SZ,f'"{exe}"')
        else:
            try:winreg.DeleteValue(key,APP_NAME)
            except FileNotFoundError:pass


def create_shortcut(exe):
    import comtypes
    from comtypes.client import CreateObject
    comtypes.CoInitialize()
    try:
        shortcut=CreateObject('WScript.Shell',dynamic=True).CreateShortcut(str(
            Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Sound Control.lnk'))
        shortcut.TargetPath=str(exe);shortcut.WorkingDirectory=str(exe.parent)
        shortcut.IconLocation=str(exe)+',0';shortcut.Save()
    finally:comtypes.CoUninitialize()


def install(pack_root,destination,startup,progress=lambda text:None):
    pack_root,destination=Path(pack_root).resolve(),Path(destination).resolve()
    source=pack_root/'SoundControlSetup.exe'
    if not source.is_file():raise FileNotFoundError('Распакуйте весь комплект Sound Control перед установкой')
    target=destination/'VoicemeeterMuteTray.exe'
    if source==target:raise ValueError('Выберите отдельную папку для установки Sound Control')
    needs_banana=detect_banana() is None
    if needs_banana:
        progress('Проверка официального пакета Banana…')
        vendor=pack_root/'vendor'/VENDOR_FILENAME
        verify_vendor(vendor)
        with tempfile.TemporaryDirectory(prefix='SoundControl-Banana-') as temp:
            with zipfile.ZipFile(vendor) as archive:archive.extract(VENDOR_SETUP,temp)
            progress('Установка Banana. Подтвердите запрос прав Windows…')
            run_vendor_setup(Path(temp)/VENDOR_SETUP)
        if detect_banana() is None:
            raise RuntimeError('После установки Banana не найдена. Проверьте результат её установщика и перезагрузите Windows')
    progress('Установка Sound Control…')
    stop_existing()
    destination.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.new')
    shutil.copyfile(source,temporary);temporary.replace(target)
    from license_info import copy_notices
    copy_notices(destination)
    from config import Settings
    from voicemeeter_right_ctrl_mute import DATA_DIR
    settings=Settings(DATA_DIR/'settings.json')
    if settings.first_run:settings.update(launch_banana=True)
    create_shortcut(target)
    set_startup(target,startup)
    return target,needs_banana


class InstallSignals(QObject):
    progress=Signal(str)
    finished=Signal(object,str)


class SetupWindow(GlassWindow):
    def __init__(self,pack_root,preview=False,banana_present=None):
        super().__init__()
        self.pack_root=Path(pack_root);self.busy=False;self.installed=None
        self.setWindowTitle('Установка Sound Control');self.setWindowIcon(application_icon(False))
        self.setMinimumSize(640,570);self.resize(700,620)
        layout=QVBoxLayout(self);layout.setContentsMargins(1,1,1,1);layout.setSpacing(0)
        layout.addWidget(TitleBar(self,'Sound Control · v'+APP_VERSION))
        body=QVBoxLayout();body.setContentsMargins(28,18,28,24);body.setSpacing(16);layout.addLayout(body)
        title=QLabel('Sound Control + VoiceMeeter Banana');title.setProperty('role','title');title.setWordWrap(True);body.addWidget(title)
        intro=QLabel('Один комплект для установки утилиты и аудиомикшера.');intro.setProperty('role','muted');intro.setWordWrap(True);body.addWidget(intro)
        card=QFrame();card.setObjectName('card');copy=QVBoxLayout(card);copy.setContentsMargins(20,18,20,18);copy.setSpacing(10)
        existing=(banana_present if preview and banana_present is not None else detect_banana())
        self.banana_present=bool(existing)
        state=QLabel('Banana уже установлена — будет использована текущая версия.' if existing else 'Banana будет установлена штатным установщиком VB-Audio.');state.setWordWrap(True);copy.addWidget(state)
        info=QLabel('Для новой установки Banana Windows запросит права администратора. После установки её аудиодрайвера потребуется перезагрузка.');info.setWordWrap(True);info.setProperty('role','muted');copy.addWidget(info)
        self.credit_label=credit=QLabel('VoiceMeeter Banana — отдельный продукт VB-Audio, donationware.')
        credit.setWordWrap(True);credit.setStyleSheet('color: '+MUTED+';');copy.addWidget(credit)
        self.links_label=links=QLabel('<a href="https://voicemeeter.com">Официальный сайт</a> · '
            '<a href="https://shop.vb-audio.com/en/win-apps/10-voicemeeter-banana.html">Поддержать автора / лицензия</a> · '
            '<a href="https://vb-audio.com/Services/licensing.htm">Условия использования</a>')
        links.setWordWrap(True);links.setOpenExternalLinks(True);links.setStyleSheet('color: '+MUTED+';');copy.addWidget(links);body.addWidget(card)
        body.addWidget(QLabel('Папка Sound Control'))
        default=Path(os.environ['LOCALAPPDATA'])/'Programs/SoundControl'
        self.path=FileButton('Папка установки',str(default),'Выберите папку')
        self.path.clicked.connect(self.choose_folder);body.addWidget(self.path)
        row=QHBoxLayout();row.addWidget(QLabel('Запускать Sound Control с Windows'),1)
        self.startup=Toggle('Запускать Sound Control с Windows');self.startup.sync(True);row.addWidget(self.startup);body.addLayout(row)
        self.status=QLabel('');self.status.setWordWrap(True);self.status.setProperty('role','muted');body.addWidget(self.status);body.addStretch()
        self.button=ActionButton('Установить Sound Control' if existing else 'Установить Sound Control и Banana')
        self.button.setProperty('accent',True);self.button.clicked.connect(self.start);body.addWidget(self.button)
        self.signals=InstallSignals(self);self.signals.progress.connect(self.status.setText);self.signals.finished.connect(self.finished)
        if preview:
            self.button.setEnabled(False)
            self.status.setText('Предпросмотр: Banana '+('установлена.' if existing else 'не установлена.')+' Установка отключена.')

    def choose_folder(self):
        path=QFileDialog.getExistingDirectory(self,'Папка Sound Control',self.path.path)
        if path:self.path.set_path(path)

    def start(self):
        if self.installed:
            subprocess.Popen([str(self.installed),'--show-settings'],creationflags=subprocess.CREATE_NO_WINDOW)
            self.close();return
        self.busy=True;self.button.setEnabled(False);self.path.setEnabled(False);self.startup.setEnabled(False)
        destination,startup=self.path.path,self.startup.isChecked()
        def work():
            try:self.signals.finished.emit(install(self.pack_root,destination,startup,self.signals.progress.emit),'')
            except Exception as exc:
                log.exception('Pack installation failed');self.signals.finished.emit(None,str(exc))
        self.thread=threading.Thread(target=work,name='Sound Control setup',daemon=True);self.thread.start()

    def finished(self,result,error):
        self.busy=False
        if error:
            self.status.setText(error);self.button.setEnabled(True);self.path.setEnabled(True);self.startup.setEnabled(True);return
        target,reboot=result
        self.status.setText('Готово. Перезагрузите Windows, чтобы включились аудиодрайверы Banana. Sound Control доступен в меню «Пуск».' if reboot else 'Sound Control установлен. Настройки сохранены.')
        if reboot:
            self.button.setText('Закрыть');self.button.clicked.disconnect();self.button.clicked.connect(self.close)
        else:self.installed=target;self.button.setText('Открыть Sound Control')
        self.button.setEnabled(True)

    def closeEvent(self,event):
        if self.busy:event.ignore()
        else:super().closeEvent(event)


def run_setup(pack_root=None,preview=False,banana_present=None):
    qt=QApplication.instance() or QApplication([])
    qt.setApplicationName('Sound Control');qt.setStyle('Fusion');qt.setFont(QFont('Segoe UI',10))
    window=SetupWindow(pack_root or Path(sys.executable).parent,preview,banana_present)
    window.frame.configure();window.show()
    return qt.exec()
