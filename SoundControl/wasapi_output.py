"""Shared-mode WASAPI output, owned exclusively by the sound thread.

Short earcons fit entirely in the initial buffer. The initialized client is
reused and stopped while idle; no exclusive device or idle silent stream.
"""
import ctypes
from ctypes import POINTER,c_ubyte,c_uint32,HRESULT
from ctypes.wintypes import DWORD
import logging
import threading
import comtypes
from comtypes import COMMETHOD,GUID,IUnknown
from pycaw.api.audioclient import IAudioClient
from pycaw.api.audioclient.depend import WAVEFORMATEX

log=logging.getLogger('VoicemeeterMuteTray')


class IAudioRenderClient(IUnknown):
    _iid_=GUID('{F294ACFC-3146-4483-A7BF-ADDCA7C260E2}')
    _methods_=(
        COMMETHOD([],HRESULT,'GetBuffer',(['in'],c_uint32,'frames'),
                  (['out'],POINTER(POINTER(c_ubyte)),'data')),
        COMMETHOD([],HRESULT,'ReleaseBuffer',(['in'],c_uint32,'frames'),(['in'],DWORD,'flags')),
    )


def guard_silence(params,frames):
    block=params.nchannels*params.sampwidth
    if params.sampwidth not in (1,2,3,4) or not 1<=params.nchannels<=8 or len(frames)%block:
        raise ValueError('Invalid PCM layout')
    silent_frame=bytes([128 if params.sampwidth==1 else 0])*block
    return silent_frame*round(params.framerate*.012)+frames+silent_frame*round(params.framerate*.025)


def render_buffered(client,renderer,buffer_frames,block_align,data,cancel,wait=None):
    """Prefill before Start; account for free frames and wait for full drain."""
    if len(data)%block_align or buffer_frames<=0:raise ValueError('Invalid render buffer')
    total=len(data)//block_align
    if not total:return {'frames':0,'writes':0,'underruns':0,'cancelled':False}
    offset,writes,underruns=0,0,0
    wait=wait or cancel.wait
    def write(count):
        nonlocal offset,writes
        pointer=renderer.GetBuffer(count)
        try:
            chunk=data[offset*block_align:(offset+count)*block_align]
            ctypes.memmove(pointer,chunk,len(chunk))
        except BaseException:
            renderer.ReleaseBuffer(count,2)
            raise
        renderer.ReleaseBuffer(count,0)
        offset+=count;writes+=1
    write(min(total,buffer_frames))
    started=False
    try:
        if cancel.is_set():return {'frames':total,'writes':writes,'underruns':0,'cancelled':True}
        client.Start();started=True
        while True:
            if cancel.is_set():return {'frames':total,'writes':writes,'underruns':underruns,'cancelled':True}
            padding=client.GetCurrentPadding()
            if padding<0 or padding>buffer_frames:raise RuntimeError('Invalid WASAPI padding')
            if offset<total:
                if padding==0:underruns+=1
                available=min(buffer_frames-padding,total-offset)
                if available:write(available)
            elif padding==0:
                break
            wait(.01)
        return {'frames':total,'writes':writes,'underruns':underruns,'cancelled':False}
    finally:
        if started:client.Stop()
        client.Reset()


class WasapiOutput:
    def __init__(self):
        self.owner=threading.get_ident()
        self.client=self.renderer=self.device=None
        self.enumerator=None
        self.identity=None
        self.buffer_frames=0
        self.opens=0
        self.last_stats={}

    def check_thread(self):
        if threading.get_ident()!=self.owner:raise RuntimeError('WASAPI used outside its owning thread')

    def ensure(self,params):
        self.check_thread()
        if self.enumerator is None:
            from pycaw.pycaw import AudioUtilities
            self.enumerator=AudioUtilities.GetDeviceEnumerator()
        device=self.enumerator.GetDefaultAudioEndpoint(0,1)
        identity=(device.GetId(),params.nchannels,params.sampwidth,params.framerate)
        if self.client is not None and self.identity==identity:return
        self.close_stream()
        self.device=device
        self.client=device.Activate(IAudioClient._iid_,comtypes.CLSCTX_ALL,None).QueryInterface(IAudioClient)
        fmt=WAVEFORMATEX()
        fmt.wFormatTag=1;fmt.nChannels=params.nchannels;fmt.nSamplesPerSec=params.framerate
        fmt.wBitsPerSample=params.sampwidth*8;fmt.nBlockAlign=params.nchannels*params.sampwidth
        fmt.nAvgBytesPerSec=fmt.nSamplesPerSec*fmt.nBlockAlign;fmt.cbSize=0
        # Shared mode, automatic channel/rate conversion with high-quality SRC.
        # 500ms holds every built-in sound + guard silence in one initial write.
        self.client.Initialize(0,0x88000000,5_000_000,0,ctypes.byref(fmt),None)
        self.buffer_frames=self.client.GetBufferSize()
        self.renderer=self.client.GetService(IAudioRenderClient._iid_).QueryInterface(IAudioRenderClient)
        self.identity=identity;self.opens+=1
        log.info('Sound output: WASAPI shared, %s Hz/%s ch/%s bit, buffer %s frames',
                 params.framerate,params.nchannels,params.sampwidth*8,self.buffer_frames)

    def play(self,params,frames,cancel):
        self.check_thread()
        data=guard_silence(params,frames)
        for attempt in range(2):
            if cancel.is_set():return
            try:
                self.ensure(params)
                self.last_stats=render_buffered(self.client,self.renderer,self.buffer_frames,
                    params.nchannels*params.sampwidth,data,cancel)
                if self.last_stats['underruns']:
                    log.warning('Sound buffer underruns: %s',self.last_stats['underruns'])
                return
            except (OSError,comtypes.COMError):
                self.close()
                if attempt:raise
                log.info('Reopening sound output after device invalidation')
                cancel.wait(.1)

    def close_stream(self):
        if self.client is not None:
            try:self.client.Stop()
            except (OSError,comtypes.COMError):pass
        self.renderer=self.client=self.device=None
        self.identity=None

    def close(self):
        self.check_thread()
        self.close_stream()
        self.enumerator=None
