import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from version import APP_VERSION
from package_source import PUBLIC_FILES
from package_release import source_revision

ROOT=Path(__file__).resolve().parent

class ReleaseTests(unittest.TestCase):
    def test_source_state_ignores_unshipped_runner_files_but_detects_source_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def git(*args):
                return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True,text=True)
            git('init','-q')
            source=root/'module.py';source.write_text('value = 1\n')
            git('add','module.py')
            git('-c','user.name=Release test','-c','user.email=test@example.invalid',
                'commit','-qm','Source fixture')
            (root/'runner.tmp').write_text('not shipped')
            self.assertEqual(source_revision(root,('module.py',))['source_state'],'clean')
            source.write_text('value = 2\n')
            self.assertEqual(source_revision(root,('module.py',))['source_state'],'uncommitted working tree')
            source.write_text('value = 1\n')
            (root/'new_source.py').write_text('new = True\n')
            revision=source_revision(root,('module.py','new_source.py'))
            self.assertEqual(revision['source_state'],'uncommitted working tree')
            self.assertEqual(revision['untracked_source_files'],['new_source.py'])

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
