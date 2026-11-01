"""Create an offline end-user pack; the official VB-Audio archive stays AS IS."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile
from installer import VENDOR_FILENAME,VENDOR_URL,VENDOR_SHA256,verify_vendor
from version import APP_VERSION

ROOT=Path(__file__).resolve().parent


def package(destination=None,download=False):
    vendor=ROOT/'vendor'/VENDOR_FILENAME
    if not vendor.is_file():
        if not download:raise FileNotFoundError('Официальный архив не найден. Запустите package_bundle.py --download')
        vendor.parent.mkdir(parents=True,exist_ok=True)
        temporary=vendor.with_suffix('.download')
        urllib.request.urlretrieve(VENDOR_URL,temporary)
        if hashlib.sha256(temporary.read_bytes()).hexdigest()!=VENDOR_SHA256:
            raise ValueError('Скачанный пакет отличается от проверенной версии VB-Audio')
        temporary.replace(vendor)
    verify_vendor(vendor)
    exe=ROOT/'dist-public/VoicemeeterMuteTray.exe'
    if not exe.is_file():raise FileNotFoundError('Сначала выполните build.ps1 -Public')
    destination=Path(destination) if destination else ROOT/f'release/SoundControl-{APP_VERSION}-windows-x64-with-Banana.zip'
    destination.parent.mkdir(parents=True,exist_ok=True)
    prefix=f'SoundControl-{APP_VERSION}-windows-x64/'
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_STORED) as archive:
        archive.write(exe,prefix+'SoundControlSetup.exe')
        archive.write(vendor,prefix+'vendor/'+VENDOR_FILENAME)
        for name in ('PACK-README.ru.md','THIRD-PARTY-NOTICES.md'):
            archive.write(ROOT/'docs'/name,prefix+name)
        archive.write(ROOT/'LICENSE',prefix+'LICENSE.txt')
        for path in (ROOT/'licenses').rglob('*'):
            if path.is_file():archive.write(path,prefix+str(path.relative_to(ROOT)).replace('\\','/'))
        archive.writestr(prefix+'components.json',json.dumps({'sound_control':APP_VERSION,
            'voicemeeter_banana':'2.1.3.0','vendor_url':VENDOR_URL,'vendor_sha256':VENDOR_SHA256,
            'sound_control_sha256':hashlib.sha256(exe.read_bytes()).hexdigest()},indent=2))
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--download',action='store_true')
    args=parser.parse_args();print(package(download=args.download))
