import re
import unittest
from pathlib import Path
from version import APP_VERSION
from package_source import PUBLIC_FILES

ROOT=Path(__file__).resolve().parent

class ReleaseTests(unittest.TestCase):
    def test_stable_version_and_windows_metadata_use_one_version_source(self):
        self.assertRegex(APP_VERSION,r'^\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?$')
        self.assertIn('from version import APP_VERSION',(ROOT/'ui_theme.py').read_text(encoding='utf-8'))
        self.assertIn('from version import APP_VERSION',(ROOT/'tools/windows_version.py').read_text(encoding='utf-8'))

    def test_source_manifest_contains_only_maintained_public_files(self):
        self.assertEqual(len(PUBLIC_FILES),len(set(PUBLIC_FILES)))
        for name in PUBLIC_FILES:
            self.assertTrue((ROOT/name).is_file(),name)
            self.assertFalse({'backups','.venv','vendor','release','.idea','.qa'} & set(Path(name).parts),name)
        for name in ('version.py','LICENSE','SoundControl.spec','license_info.py','package_release.py'):
            self.assertIn(name,PUBLIC_FILES)

    def test_release_notes_and_key_documentation_exist(self):
        for name in ('README.md','docs/USER-GUIDE.md','docs/BUILDING.md','docs/RELEASING.md',
                     'docs/releases/'+APP_VERSION+'.md','docs/THIRD-PARTY-NOTICES.md'):
            self.assertTrue((ROOT/name).is_file(),name)

if __name__=='__main__':unittest.main()
