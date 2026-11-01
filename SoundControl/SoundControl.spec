# PyInstaller recipe. Only the Qt Widgets/SVG components used by this app ship.
from pathlib import Path
root = Path(SPECPATH)
qt_dlls = {'qt6core.dll','qt6gui.dll','qt6widgets.dll','qt6network.dll','qt6svg.dll','qt6opengl.dll'}
plugins = {'platforms/qwindows.dll','platforms/qoffscreen.dll','platforms/qminimal.dll',
           'styles/qmodernwindowsstyle.dll','iconengines/qsvgicon.dll','imageformats/qsvg.dll','imageformats/qico.dll'}
def required_binary(entry):
    name = entry[0].replace('\\','/').lower()
    base = name.rsplit('/',1)[-1]
    if base.startswith('qt6') and base.endswith('.dll'):
        return base in qt_dlls
    if '/plugins/' in name and name.startswith('pyside6/'):
        return name.split('/plugins/',1)[1] in plugins
    return True
a = Analysis([str(root/'voicemeeter_right_ctrl_mute.py')],pathex=[str(root)],
    binaries=[],datas=[(str(root/'audio_assets'),'audio_assets'),(str(root/'licenses'),'licenses'),
                       (str(root/'LICENSE'),'.'),(str(root/'docs/THIRD-PARTY-NOTICES.md'),'docs')],
    hiddenimports=[],hookspath=[],hooksconfig={},runtime_hooks=[],
    excludes=['PySide6.QtPdf','PySide6.QtQml','PySide6.QtQuick','PySide6.QtVirtualKeyboard'],
    noarchive=False,optimize=0)
a.binaries = [entry for entry in a.binaries if required_binary(entry)]
a.datas = [entry for entry in a.datas if not entry[0].replace('\\','/').lower().startswith('pyside6/qml/')]
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,a.binaries,a.datas,[],name='VoicemeeterMuteTray',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    disable_windowed_traceback=False,icon=str(root/'app.ico'),version=str(root/'build/windows-version.txt'))
