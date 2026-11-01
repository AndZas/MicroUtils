import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import zipfile
from config import Settings
import installer


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.pack=self.root/'pack';self.pack.mkdir()
        (self.pack/'SoundControlSetup.exe').write_bytes(b'MZ mock Sound Control')
        self.destination=self.root/'installed'
        self.profile=self.root/'profile'

    def vendor(self):
        path=self.pack/'vendor'/installer.VENDOR_FILENAME;path.parent.mkdir()
        with zipfile.ZipFile(path,'w') as z:z.writestr(installer.VENDOR_SETUP,b'MZ mock VB-Audio')
        return path,hashlib.sha256(path.read_bytes()).hexdigest()

    def test_existing_banana_is_reused_without_running_vendor_or_losing_preferences(self):
        settings=Settings(self.profile/'settings.json');settings.update(mute_volume=23,launch_banana=False,hotkey='f9')
        before=settings.to_dict()
        with patch('installer.detect_banana',return_value=self.root/'existing-banana'), \
             patch('installer.run_vendor_setup') as run,patch('installer.stop_existing') as stop, \
             patch('installer.create_shortcut') as shortcut,patch('installer.set_startup') as startup, \
             patch('voicemeeter_right_ctrl_mute.DATA_DIR',self.profile):
            target,reboot=installer.install(self.pack,self.destination,False)
        run.assert_not_called();stop.assert_called_once()
        self.assertFalse(reboot)
        self.assertEqual(target.read_bytes(),(self.pack/'SoundControlSetup.exe').read_bytes())
        self.assertEqual(Settings(self.profile/'settings.json').to_dict(),before)
        shortcut.assert_called_once_with(target);startup.assert_called_once_with(target,False)

    def test_new_banana_uses_verified_original_setup_and_requests_reboot(self):
        path,sha=self.vendor()
        with patch('installer.VENDOR_SHA256',sha), \
             patch('installer.detect_banana',side_effect=[None,self.root/'new-banana']), \
             patch('installer.run_vendor_setup') as run,patch('installer.stop_existing'), \
             patch('installer.create_shortcut'),patch('installer.set_startup'), \
             patch('voicemeeter_right_ctrl_mute.DATA_DIR',self.profile):
            target,reboot=installer.install(self.pack,self.destination,True)
        self.assertTrue(reboot);self.assertTrue(target.is_file())
        self.assertEqual(run.call_args.args[0].name,installer.VENDOR_SETUP)
        self.assertTrue(Settings(self.profile/'settings.json').launch_banana)

    def test_cancelled_uac_does_not_copy_app_or_stop_the_working_instance(self):
        path,sha=self.vendor()
        with patch('installer.VENDOR_SHA256',sha),patch('installer.detect_banana',return_value=None), \
             patch('installer.run_vendor_setup',side_effect=RuntimeError('Cancelled')), \
             patch('installer.stop_existing') as stop:
            with self.assertRaisesRegex(RuntimeError,'Cancelled'):
                installer.install(self.pack,self.destination,True)
        self.assertFalse(self.destination.exists());stop.assert_not_called()

    def test_vendor_integrity_and_archive_members_are_checked_before_execution(self):
        path,sha=self.vendor()
        with self.assertRaisesRegex(ValueError,'повреждён'):installer.verify_vendor(path)
        with patch('installer.VENDOR_SHA256',sha):installer.verify_vendor(path)
        with zipfile.ZipFile(path,'a') as z:z.writestr('../escape.exe',b'bad')
        with patch('installer.VENDOR_SHA256',hashlib.sha256(path.read_bytes()).hexdigest()):
            with self.assertRaisesRegex(ValueError,'состав'):installer.verify_vendor(path)


if __name__=='__main__':unittest.main()
