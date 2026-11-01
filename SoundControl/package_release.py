"""Prepare local release assets. Never stages, commits, tags or uploads anything."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import urllib.request
import zipfile
from version import APP_VERSION
from package_source import package as source_package, PUBLIC_FILES
from package_bundle import package as bundle_package

ROOT=Path(__file__).resolve().parent
QT_DLLS={'qt6core.dll','qt6gui.dll','qt6widgets.dll','qt6network.dll','qt6svg.dll','qt6opengl.dll'}

def source_revision(root=ROOT,public_files=None):
    """Describe packaged source, excluding unrelated untracked runner files."""
    public_files=PUBLIC_FILES if public_files is None else public_files
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True)
    status=subprocess.run(['git','status','--porcelain','--untracked-files=no','--','.'],
                          cwd=root,capture_output=True,text=True)
    tracked=subprocess.run(['git','ls-files','-z'],cwd=root,capture_output=True,text=True)
    generated={'licenses/DEPENDENCIES.json','licenses/Python.txt'}
    untracked=sorted(set(public_files)-set(tracked.stdout.split('\0'))-generated)
    clean=(head.returncode==status.returncode==tracked.returncode==0
           and not status.stdout and not untracked)
    return {'git_commit':head.stdout.strip() if head.returncode==0 else None,
            'source_state':'clean' if clean else 'uncommitted working tree',
            'source_changes':status.stdout.splitlines(),'untracked_source_files':untracked}

def check_binary(exe):
    from PyInstaller.archive.readers import CArchiveReader
    import pefile
    pe=pefile.PE(str(exe))
    if pe.FILE_HEADER.Machine!=0x8664:raise ValueError('Only the validated x64 build is released')
    info=pe.VS_FIXEDFILEINFO[0]
    version=(info.FileVersionMS>>16,info.FileVersionMS&65535,info.FileVersionLS>>16)
    if version!=tuple(map(int,APP_VERSION.split('-')[0].split('.'))):raise ValueError('PE version does not match source version')
    pe.close()
    archive=CArchiveReader(str(exe));names=set(archive.toc)
    qt={Path(n.replace('\\','/')).name.lower() for n in names if Path(n.replace('\\','/')).name.lower().startswith('qt6') and n.lower().endswith('.dll')}
    if qt-QT_DLLS:raise ValueError('Unapproved Qt modules in executable: '+str(qt-QT_DLLS))
    if not {'qt6core.dll','qt6gui.dll','qt6widgets.dll','qt6svg.dll'}<=qt:raise ValueError('Required Qt module missing')
    wavs={n.replace('\\','/') for n in names if n.lower().endswith('.wav')}
    if wavs!={'audio_assets/'+kind+'.wav' for kind in ('mute','unmute','notice')}:raise ValueError('Unexpected sound assets')
    if any(Path(n.replace('\\','/')).name.lower()=='icuuc.dll' for n in names):raise ValueError('Unexpected ICU DLL')
    if not any(n.startswith('licenses') and 'LGPL-3.0-only' in n for n in names):raise ValueError('Missing LGPL notice')

def qt_sources(destination,download):
    sources=json.loads((ROOT/'docs/QT-SOURCES.json').read_text(encoding='utf-8'))
    folder=ROOT/'vendor/qt-sources';folder.mkdir(parents=True,exist_ok=True)
    for item in sources:
        path=folder/item['file']
        if not path.is_file():
            if not download:raise FileNotFoundError('Use --download to obtain Qt source archives')
            temporary=path.with_suffix('.download')
            urllib.request.urlretrieve(item['url'],temporary);temporary.replace(path)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Qt source checksum mismatch: '+path.name)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_STORED) as archive:
        for item in sources:archive.write(folder/item['file'],item['file'])
        archive.write(ROOT/'docs/QT-SOURCES.json','QT-SOURCES.json')
        for name in ('LGPL-3.0-only.txt','GPL-3.0-only.txt'):archive.write(ROOT/'licenses'/name,name)
    return destination

def package(download=False):
    exe=ROOT/'dist-public/VoicemeeterMuteTray.exe'
    check_binary(exe)
    folder=ROOT/'release';folder.mkdir(exist_ok=True)
    base=f'SoundControl-{APP_VERSION}-windows-x64'
    standalone=folder/(base+'.exe');shutil.copy2(exe,standalone)
    portable=folder/(base+'-portable.zip')
    with zipfile.ZipFile(portable,'w',zipfile.ZIP_DEFLATED) as archive:
        prefix=base+'/'
        archive.write(exe,prefix+'VoicemeeterMuteTray.exe')
        archive.write(ROOT/'docs/PORTABLE-README.ru.md',prefix+'README.ru.md')
        archive.write(ROOT/'docs/THIRD-PARTY-NOTICES.md',prefix+'THIRD-PARTY-NOTICES.md')
        archive.write(ROOT/'LICENSE',prefix+'LICENSE.txt')
        for path in (ROOT/'licenses').rglob('*'):
            if path.is_file():archive.write(path,prefix+str(path.relative_to(ROOT)).replace('\\','/'))
    files=[standalone,portable,bundle_package(download=download),source_package(),
           qt_sources(folder/f'SoundControl-{APP_VERSION}-qt-sources.zip',download)]
    dependencies=json.loads((ROOT/'licenses/DEPENDENCIES.json').read_text(encoding='utf-8'))
    info=folder/f'SoundControl-{APP_VERSION}-build-info.json'
    info.write_text(json.dumps({'version':APP_VERSION,'platform':'windows-x64','python':platform.python_version(),
        **source_revision(),
        'dependencies':dependencies,'exe_sha256':hashlib.sha256(exe.read_bytes()).hexdigest()},indent=2)+'\n',encoding='utf-8')
    files.append(info)
    checksums=folder/'SHA256SUMS.txt'
    checksums.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in files),encoding='utf-8')
    manifest=folder/'release-assets.json'
    manifest.write_text(json.dumps({'version':APP_VERSION,'tag':'sound-control-v'+APP_VERSION,
        'assets':[p.name for p in files+[checksums]]},indent=2)+'\n',encoding='utf-8')
    return files+[checksums]

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--download',action='store_true')
    args=parser.parse_args()
    for asset in package(args.download):print(asset)
