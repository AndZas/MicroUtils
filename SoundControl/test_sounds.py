import hashlib
import json
from pathlib import Path
import tempfile
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
import wave
from config import Settings
import sounds
from generate_sounds import synthesize,generate


class SoundTests(unittest.TestCase):
    def test_mic_earcons_are_two_steady_notes_in_opposite_order(self):
        from array import array
        for kind,expected in [('unmute',(659.255,880)),('mute',(880,659.255))]:
            samples=array('h');samples.frombytes(synthesize(kind))
            for start,frequency in zip((.02,.135),expected):
                window=samples[round(start*48000):round((start+.04)*48000)]
                crossings=sum(a<=0<b for a,b in zip(window,window[1:]))
                self.assertAlmostEqual(crossings/.04,frequency,delta=30)
            self.assertTrue(all(v==0 for v in samples[round(.225*48000):]))
            self.assertTrue(all(v==0 for v in samples[round(.092*48000):round(.113*48000)]))

    def test_playback_waits_for_waveform_end_instead_of_cutting_it(self):
        with tempfile.TemporaryDirectory() as directory,patch('sounds._player') as player:
            sounds._play('','unmute',70,Path(directory))
        player.play.assert_called_once()
        params,frames,cancel=player.play.call_args.args
        self.assertEqual(params.framerate,48000)
        self.assertEqual(len(frames),round(.24*48000)*2)
        self.assertIs(cancel,sounds._reset)

    def test_repeated_requests_finish_current_sound_and_keep_only_latest_pending(self):
        entered,release,completed=threading.Event(),threading.Event(),threading.Event()
        received=[]
        def playback(path,*args):
            received.append(path)
            if path=='first':
                entered.set();release.wait(2)
            else:completed.set()
        with tempfile.TemporaryDirectory() as directory, \
             patch('sounds._requests',queue.Queue(maxsize=1)), \
             patch('sounds._cache',SimpleNamespace(name=directory)), \
             patch('sounds._closed',False),patch('sounds._play',side_effect=playback), \
             patch('sounds.WasapiOutput'):
            worker=threading.Thread(target=sounds._run)
            with patch('sounds._worker',worker):
                worker.start()
                try:
                    sounds.play('first')
                    self.assertTrue(entered.wait(1))
                    for index in range(100):sounds.play(str(index))
                    self.assertEqual(received,['first'])
                    release.set();self.assertTrue(completed.wait(1))
                    sounds._requests.put(None)
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                finally:
                    release.set()
                    if worker.is_alive():sounds._requests.put(None);worker.join(2)
            self.assertEqual(received,['first','99'])

    def test_pcm_gain_and_zero_for_all_supported_sample_widths(self):
        for width in (1,2,3,4):
            if width==1:
                self.assertEqual(sounds.scale_pcm(bytes([0,128,255]),1,.5),bytes([64,128,192]))
                self.assertEqual(sounds.scale_pcm(bytes([0,255]),1,0),bytes([128,128]))
            else:
                limit=1<<(width*8-1)
                frames=b''.join(v.to_bytes(width,'little',signed=True) for v in (-limit,0,limit-1))
                actual=sounds.scale_pcm(frames,width,.5)
                values=[int.from_bytes(actual[i:i+width],'little',signed=True) for i in range(0,len(actual),width)]
                self.assertEqual(values,[-limit//2,0,limit//2])
                self.assertEqual(sounds.scale_pcm(frames,width,0),bytes(len(frames)))
                self.assertEqual(sounds.scale_pcm(frames,width,1),frames)

    def test_defaults_are_original_short_distinct_click_free_pcm(self):
        with tempfile.TemporaryDirectory() as directory:
            generate(directory)
            hashes=set()
            for kind in ('mute','unmute','notice'):
                path=Path(directory)/(kind+'.wav')
                with wave.open(str(path)) as audio:
                    self.assertEqual((audio.getnchannels(),audio.getsampwidth(),audio.getframerate()),(1,2,48000))
                    self.assertLess(audio.getnframes()/48000,.4)
                    data=audio.readframes(audio.getnframes())
                self.assertEqual(data,synthesize(kind))
                self.assertEqual(int.from_bytes(data[:2],'little',signed=True),0)
                self.assertLess(abs(int.from_bytes(data[-2:],'little',signed=True)),20)
                hashes.add(hashlib.sha256(data).hexdigest())
            self.assertEqual(len(hashes),3)

    def test_volume_cache_applies_gain_without_altering_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);generate(root)
            cache=root/'cache';cache.mkdir()
            source=root/'mute.wav';before=source.read_bytes()
            full=sounds.prepared_file(source,100,cache)
            half=sounds.prepared_file(source,50,cache)
            self.assertEqual(half,sounds.prepared_file(source,50,cache))
            self.assertNotEqual(full,half)
            with wave.open(str(full)) as audio:a=audio.readframes(audio.getnframes())
            with wave.open(str(half)) as audio:b=audio.readframes(audio.getnframes())
            self.assertEqual(b,sounds.scale_pcm(a,2,.25))
            self.assertEqual(source.read_bytes(),before)

    def test_bad_custom_file_uses_own_notice_and_never_system_beep(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bad=root/'bad.wav';bad.write_bytes(b'not wav')
            with patch('sounds._player') as player:
                sounds._play(str(bad),'notice',65,root)
                player.play.assert_called_once()
                params,frames,_=player.play.call_args.args
                self.assertEqual(len(frames),round(.32*48000)*2)
                self.assertEqual(params.framerate,48000)

    def test_reset_is_not_lost_if_new_sound_replaces_the_reset_queue_marker(self):
        entered,release,handled=threading.Event(),threading.Event(),threading.Event()
        player=Mock()
        def playback(path,*args):
            if path=='before-sleep':entered.set();release.wait(2)
            else:
                self.assertFalse(sounds._reset.is_set())
                self.assertEqual(player.close.call_count,1)
                handled.set()
        with tempfile.TemporaryDirectory() as directory, \
             patch('sounds._requests',queue.Queue(maxsize=1)),patch('sounds._reset',threading.Event()), \
             patch('sounds._cache',SimpleNamespace(name=directory)),patch('sounds._closed',False), \
             patch('sounds.WasapiOutput',return_value=player),patch('sounds._play',side_effect=playback):
            worker=threading.Thread(target=sounds._run)
            with patch('sounds._worker',worker):
                worker.start()
                try:
                    sounds.play('before-sleep');self.assertTrue(entered.wait(1))
                    sounds.reset_output()
                    sounds.play('after-resume')  # Replaces the marker, but not the flag.
                    release.set();self.assertTrue(handled.wait(1))
                    sounds._requests.put(None);worker.join(2)
                    self.assertFalse(worker.is_alive())
                finally:
                    release.set()
                    if worker.is_alive():sounds._requests.put(None);worker.join(2)
            self.assertFalse(sounds._reset.is_set())

    def test_zero_volume_does_not_start_playback(self):
        with patch('sounds._play') as play:
            sounds.play('',volume=0)
            play.assert_not_called()

    def test_v2_migration_preserves_custom_wav_and_non_audio_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'mic_on.wav';source.write_bytes(b'custom sample')
            path=root/'settings.json'
            data={'schema_version':2,'sound_on_file':str(source),'ui_mode':'window',
                  'mute_sound':False,'auto_repair':False,'launch_banana':False,'notification_sound':True}
            path.write_text(json.dumps(data))
            settings=Settings(path)
            self.assertEqual(settings.sound_on_file,str(source))
            self.assertEqual(settings.mute_volume,70)
            for key in ('ui_mode','mute_sound','auto_repair','launch_banana','notification_sound'):
                self.assertEqual(getattr(settings,key),data[key])
            self.assertTrue(path.with_suffix('.v2.backup.json').exists())

    def test_v2_migration_replaces_only_known_shipped_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'old.wav';source.write_bytes(b'old shipped sample')
            path=root/'settings.json';path.write_text(json.dumps({'schema_version':2,'sound_on_file':str(source)}))
            known={hashlib.sha256(source.read_bytes()).hexdigest()}
            with patch('config.LEGACY_SOUND_HASHES',known):settings=Settings(path)
            self.assertEqual(settings.sound_on_file,'')
            self.assertTrue(source.exists())


if __name__=='__main__':unittest.main()
