"""Produce an allowlisted source archive suitable for a utilities monorepo."""
import json
from pathlib import Path
import zipfile
from config import DEFAULTS, SCHEMA_VERSION
from version import APP_VERSION

ROOT = Path(__file__).resolve().parent
PUBLIC_FILES = (
    '.gitignore', 'README.md', 'config.example.json', 'requirements.txt', 'build.ps1',
    'package_source.py', 'app.ico', 'voicemeeter_right_ctrl_mute.py', 'config.py',
    'generate_sounds.py','wasapi_output.py','test_wasapi.py','audio_assets/README.md','audio_assets/mute.wav','audio_assets/unmute.wav','audio_assets/notice.wav',
    'installer.py','package_bundle.py','docs/PACK-README.ru.md','docs/THIRD-PARTY-NOTICES.md','test_sounds.py','test_installer.py',
    'hotkeys.py', 'sounds.py', 'app_icons.py', 'power_lifecycle.py', 'settings_window.py', 'quiet_notice.py', 'ui_theme.py', 'window_frame.py', 'tray_menu.py', 'local_control.py', 'mic_recovery.py',
    'bluetooth_outputs.py', 'banana_host.py', 'test_config.py', 'test_hotkeys.py',
    'test_general_settings.py', 'test_power.py', 'test_qt_ui.py', 'test_local_control.py', 'test_package.py', 'test_muter.py', 'test_recovery.py', 'test_bluetooth.py',
    'test_banana_host.py','version.py','LICENSE','SoundControl.spec','license_info.py',
    'package_release.py','test_release.py',
) + tuple(sorted(str(p.relative_to(ROOT)).replace('\\','/') for directory in ('docs','licenses','tools')
                 for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts
                 and (directory=='licenses' or p.suffix.lower() in ('.md','.json','.txt','.png','.svg','.py','.ps1'))))
PUBLIC_FILES = tuple(dict.fromkeys(PUBLIC_FILES))
SOURCE_PREFIX = f'SoundControl-{APP_VERSION}-source/'


def package(destination=None):
    sample = dict(DEFAULTS, schema_version=SCHEMA_VERSION, target=None)
    (ROOT/'config.example.json').write_text(json.dumps(sample, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    destination = Path(destination) if destination else ROOT/f'release/SoundControl-{APP_VERSION}-source.zip'
    missing = [name for name in PUBLIC_FILES if not (ROOT/name).is_file()]
    if missing:
        raise FileNotFoundError(', '.join(missing))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in PUBLIC_FILES:
            archive.write(ROOT/name, SOURCE_PREFIX+name)
    return destination


if __name__ == '__main__':
    print(package())
