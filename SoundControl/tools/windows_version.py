"""Generate PE metadata from version.py; no second manually edited version."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from version import APP_VERSION

def generate():
    numbers=tuple(map(int,APP_VERSION.split('-')[0].split('.')))+(0,)
    text=f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r}, mask=0x3f, flags=0x0,
                   OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
  kids=[StringFileInfo([StringTable('040904B0',[
    StringStruct('CompanyName','AndZas'),
    StringStruct('FileDescription','Sound Control'),
    StringStruct('FileVersion',{APP_VERSION!r}),
    StringStruct('InternalName','SoundControl'),
    StringStruct('LegalCopyright','Copyright (c) 2026 AndZas. MIT. Qt/PySide6: LGPLv3.'),
    StringStruct('OriginalFilename','VoicemeeterMuteTray.exe'),
    StringStruct('ProductName','Sound Control'),
    StringStruct('ProductVersion',{APP_VERSION!r})
  ])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])
'''
    output=ROOT/'build/windows-version.txt';output.parent.mkdir(exist_ok=True)
    output.write_text(text,encoding='utf-8')
    return output

if __name__=='__main__':print(generate())
