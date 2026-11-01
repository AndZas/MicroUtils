import json
from pathlib import PurePosixPath, Path
import tempfile
import unittest
import zipfile
from package_source import package, PUBLIC_FILES, SOURCE_PREFIX


class PackageTests(unittest.TestCase):
    def test_source_package_is_allowlisted_and_has_clean_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path=package(Path(directory)/'source.zip')
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(set(archive.namelist()),{SOURCE_PREFIX+name for name in PUBLIC_FILES})
                for name in archive.namelist():
                    parts=PurePosixPath(name).parts
                    self.assertFalse({'backups','.venv','build','dist','staging','release'} & set(parts))
                    self.assertNotIn('..',parts)
                    self.assertFalse(name.endswith(('.log','.lnk')))
                    if name.endswith('.wav'):self.assertIn(PurePosixPath(name).name,{'mute.wav','unmute.wav','notice.wav'})
                settings=json.loads(archive.read(SOURCE_PREFIX+'config.example.json'))
                self.assertNotIn(b'\r',archive.read(SOURCE_PREFIX+'config.example.json'))
                self.assertIsNone(settings['target'])
                self.assertFalse(settings['launch_banana'])
                self.assertFalse(settings['hotkey_enabled'])
                for field in ('banana_exe','sound_on_file','sound_off_file','notification_sound_file'):
                    self.assertEqual(settings[field],'')


if __name__=='__main__':unittest.main()
