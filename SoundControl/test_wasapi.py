import ctypes
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from wasapi_output import WasapiOutput,render_buffered,guard_silence


class FakeRender:
    def __init__(self,padding,capacity=500):
        self.padding=iter(padding);self.capacity=capacity;self.queued=0
        self.blocks=[];self.timeline=[];self.client=self
    def GetBuffer(self,frames):
        assert self.queued+frames<=self.capacity
        self.memory=ctypes.create_string_buffer(frames*2)
        self.timeline.append(('write',frames))
        return ctypes.addressof(self.memory)
    def ReleaseBuffer(self,frames,flags):
        self.blocks.append(self.memory.raw);self.queued+=frames
    def Start(self):self.timeline.append(('start',self.queued))
    def GetCurrentPadding(self):
        self.queued=next(self.padding);return self.queued
    def Stop(self):self.timeline.append(('stop',self.queued))
    def Reset(self):self.timeline.append(('reset',self.queued));self.queued=0


class WasapiTests(unittest.TestCase):
    def test_short_sound_is_fully_buffered_before_start_and_drained_before_stop(self):
        fake=FakeRender([100,0]);data=b'\x12\x34'*200
        stats=render_buffered(fake,fake,500,2,data,threading.Event(),wait=lambda seconds:None)
        self.assertEqual(fake.timeline[:2],[('write',200),('start',200)])
        self.assertEqual(fake.timeline[-2:],[('stop',0),('reset',0)])
        self.assertEqual(b''.join(fake.blocks),data)
        self.assertEqual(stats,{'frames':200,'writes':1,'underruns':0,'cancelled':False})

    def test_long_custom_file_refills_only_free_frames_without_truncation(self):
        fake=FakeRender([300,100,200,0]);data=b'\x12\x34'*1100
        stats=render_buffered(fake,fake,500,2,data,threading.Event(),wait=lambda seconds:None)
        self.assertEqual(b''.join(fake.blocks),data)
        self.assertEqual(stats['writes'],3)
        self.assertEqual(stats['underruns'],0)
        self.assertEqual(fake.timeline[-2:],[('stop',0),('reset',0)])

    def test_cancellation_stops_and_resets_without_replaying(self):
        cancel=threading.Event();fake=FakeRender([100,0])
        stats=render_buffered(fake,fake,500,2,b'\0\0'*200,cancel,wait=lambda seconds:cancel.set())
        self.assertTrue(stats['cancelled'])
        self.assertEqual([kind for kind,_ in fake.timeline].count('start'),1)
        self.assertEqual(fake.timeline[-1][0],'reset')

    def test_guard_silence_preserves_waveform_and_matches_unsigned_pcm(self):
        for width in (1,2,3,4):
            params=SimpleNamespace(nchannels=2,sampwidth=width,framerate=48000)
            data=bytes(range(32))*(width*2)
            output=guard_silence(params,data)
            head=round(.012*48000)*width*2
            self.assertEqual(output[head:head+len(data)],data)
            self.assertEqual(set(output[:head]),{128 if width==1 else 0})

    def test_same_format_and_default_endpoint_reuse_initialized_client(self):
        output=WasapiOutput();client=Mock();renderer=Mock()
        client.GetBufferSize.return_value=24000
        client.GetService.return_value.QueryInterface.return_value=renderer
        device=Mock();device.GetId.return_value='endpoint-a'
        device.Activate.return_value.QueryInterface.return_value=client
        output.enumerator=Mock();output.enumerator.GetDefaultAudioEndpoint.return_value=device
        params=SimpleNamespace(nchannels=1,sampwidth=2,framerate=48000)
        output.ensure(params);output.ensure(params)
        self.assertEqual(output.opens,1);client.Initialize.assert_called_once()
        self.assertEqual(client.Initialize.call_args.args[:4],(0,0x88000000,5000000,0))
        device.GetId.return_value='endpoint-b'
        output.ensure(params)
        self.assertEqual(output.opens,2)
        output.close()


if __name__=='__main__':unittest.main()
