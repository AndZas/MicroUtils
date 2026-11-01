"""Copy the exact runtime distribution notices into the release; no private paths."""
import importlib.metadata as metadata
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
PACKAGES=('keyboard','Pillow','pycaw','comtypes','psutil','PySide6-Essentials','shiboken6','PyInstaller')

def collect():
    folder=ROOT/'licenses';folder.mkdir(exist_ok=True)
    versions={}
    for package in PACKAGES:
        dist=metadata.distribution(package);versions[package]=dist.version
        candidates=[p for p in dist.files or [] if Path(p).name.lower() in ('license','license.txt','copying.txt')
                    or ('licenses/' in str(p).replace('\\','/') and str(p).endswith('.txt'))]
        if not candidates:raise RuntimeError('No licence notice for '+package)
        content='\n\n'.join(dist.locate_file(path).read_text(encoding='utf-8',errors='replace') for path in candidates)
        (folder/(package+'.txt')).write_text(content,encoding='utf-8')
    (folder/'Python.txt').write_bytes((Path(sys.base_prefix)/'LICENSE.txt').read_bytes())
    versions['Python']=sys.version.split()[0]
    (folder/'DEPENDENCIES.json').write_text(json.dumps(versions,indent=2)+'\n',encoding='utf-8')
    return folder

if __name__=='__main__':print(collect())
