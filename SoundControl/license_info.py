"""Expose the bundled third-party notices independently of one-file extraction."""
import os
from pathlib import Path
import shutil

def copy_notices(destination):
    root=Path(__file__).resolve().parent
    target=Path(destination);target.mkdir(parents=True,exist_ok=True)
    shutil.copytree(root/'licenses',target/'licenses',dirs_exist_ok=True)
    shutil.copyfile(root/'LICENSE',target/'LICENSE.txt')
    shutil.copyfile(root/'docs/THIRD-PARTY-NOTICES.md',target/'THIRD-PARTY-NOTICES.md')
    return target

def open_notices():
    target=copy_notices(Path(os.environ['LOCALAPPDATA'])/'VoicemeeterMuteTray'/'licenses')
    os.startfile(target)
