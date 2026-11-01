import json
from pathlib import Path
import tempfile
import unittest
from config import Settings, SCHEMA_VERSION


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'settings.json'

    def test_new_user_is_not_bound_to_personal_profile(self):
        s=Settings(self.path)
        self.assertTrue(s.first_run)
        self.assertEqual(s.ui_mode,'both')
        self.assertIsNone(s.target)
        for key in ('hotkey_enabled','auto_repair','output_reconnect','launch_banana','hide_banana_tray'):
            self.assertFalse(getattr(s,key),key)

    def test_legacy_profile_migration_keeps_behavior_and_device(self):
        data={'hotkey_enabled':True,'auto_repair':True,'bluetooth_restart':True,
              'launch_banana':True,'hide_banana_tray':True,'notifications':False,
              'target':{'name':'Example microphone','driver':'wdm','endpoint_id':'example-id'}}
        self.path.write_text(json.dumps(data),encoding='utf-8')
        s=Settings(self.path)
        self.assertTrue(s.migrated)
        self.assertEqual(s.hotkey,'right ctrl')
        self.assertEqual(s.output_bus_indices,[1,2])
        self.assertEqual(s.ui_mode,'tray')
        self.assertEqual(s.notice_seconds,0)
        self.assertEqual(s.target.endpoint_id,'example-id')
        self.assertTrue(s.output_reconnect)
        self.assertFalse(s.notifications)
        self.assertTrue(self.path.with_suffix('.v1.backup.json').exists())
        s.save(); again=Settings(self.path)
        self.assertFalse(again.migrated)
        self.assertEqual(again.target,s.target)

    def test_invalid_updates_do_not_mutate_or_write(self):
        s=Settings(self.path);s.save();old=self.path.read_bytes()
        for kwargs in ({'strip_index':3},{'strip_index':True},{'notice_seconds':float('nan')},
                       {'output_bus_indices':[0,3]},{'ui_mode':'invalid'},{'auto_repair':'false'},
                       {'output_settle_seconds':0},{'unknown':True},{'hotkey':'no-such-key-name'}):
            with self.assertRaises(ValueError):s.update(**kwargs)
        self.assertEqual(self.path.read_bytes(),old)

    def test_roundtrip_all_generalized_fields(self):
        s=Settings(self.path)
        s.update(hotkey='ctrl+shift+m',strip_index=2,output_device_scope='all',
                 output_bus_indices=[2,0,2],notification_sound=True,notice_seconds=4,
                 mute_sound=False,ui_mode='window',close_to_tray=False,output_settle_seconds=4)
        t=Settings(self.path)
        self.assertEqual(s.to_dict(),t.to_dict())
        self.assertEqual(t.output_bus_indices,[0,2])
        self.assertEqual(json.loads(self.path.read_text())['schema_version'],SCHEMA_VERSION)

    def test_corrupt_profile_is_backed_up_before_recovery(self):
        self.path.write_text('broken JSON',encoding='utf-8')
        s=Settings(self.path);s.save()
        self.assertEqual(self.path.with_suffix('.invalid.backup.json').read_text(),'broken JSON')

    def test_future_schema_is_not_overwritten(self):
        self.path.write_text('{"schema_version":999}',encoding='utf-8')
        with self.assertRaises(RuntimeError):Settings(self.path)
        self.assertIn('999',self.path.read_text())

    def test_invalid_loaded_hotkey_is_disabled(self):
        self.path.write_text('{"schema_version":2,"hotkey":"no-such-key","hotkey_enabled":true}',encoding='utf-8')
        self.assertFalse(Settings(self.path).hotkey_enabled)


if __name__=='__main__':unittest.main()
