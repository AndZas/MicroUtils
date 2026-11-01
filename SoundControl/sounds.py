"""Independent PCM gain and prebuffered WASAPI playback on one background thread."""
from array import array
import atexit
import hashlib
import logging
from pathlib import Path
import queue
import sys
import tempfile
import threading
import wave
import comtypes
from wasapi_output import WasapiOutput

log = logging.getLogger("VoicemeeterMuteTray")
ASSETS = Path(__file__).resolve().with_name('audio_assets')
_requests = queue.Queue(maxsize=1)
_lock = threading.Lock()
_worker = None
_cache = None
_closed = False
_player = None
_reset = threading.Event()
_RESET = object()


def scale_pcm(frames, width, gain):
    """PCM 8/16/24/32-bit, preserving format/channels and clipping bounds."""
    if width not in (1,2,3,4) or len(frames)%width:
        raise ValueError('Поддерживается PCM WAV 8/16/24/32 бит')
    if gain == 1:return frames
    if width == 1:
        return bytes(max(0,min(255,round((sample-128)*gain)+128)) for sample in frames)
    if width in (2,4):
        samples=array('h' if width==2 else 'i')
        samples.frombytes(frames)
        if sys.byteorder!='little':samples.byteswap()
        low,high=-(1<<(width*8-1)),(1<<(width*8-1))-1
        for i,sample in enumerate(samples):samples[i]=max(low,min(high,round(sample*gain)))
        if sys.byteorder!='little':samples.byteswap()
        return samples.tobytes()
    output=bytearray(len(frames))
    for i in range(0,len(frames),3):
        sample=int.from_bytes(frames[i:i+3],'little',signed=True)
        output[i:i+3]=max(-8388608,min(8388607,round(sample*gain))).to_bytes(3,'little',signed=True)
    return bytes(output)


def prepared_file(source, volume, folder):
    stat=source.stat()
    if stat.st_size > 32*1024*1024:
        raise ValueError('Для сигнала используйте WAV меньше 32 МБ')
    key=hashlib.sha256(f'{source.resolve()}:{stat.st_mtime_ns}:{stat.st_size}:{volume}'.encode()).hexdigest()
    target=folder/(key+'.wav')
    if target.exists():return target
    with wave.open(str(source),'rb') as audio:
        params=audio.getparams()
        frames=audio.readframes(audio.getnframes())
    frames=scale_pcm(frames,params.sampwidth,(volume/100)**2)
    # Bound this dedicated ephemeral cache, never the user's original files.
    previous=sorted(folder.glob('*.wav'),key=lambda p:p.stat().st_mtime)
    while previous and (len(previous)>=16 or sum(p.stat().st_size for p in previous)>32*1024*1024):
        try:previous[0].unlink()
        except OSError:pass
        previous.pop(0)
    with wave.open(str(target),'wb') as output:
        output.setparams(params)
        output.writeframes(frames)
    return target


def _play(path, kind, volume, folder):
    fallback=ASSETS/((kind if kind in ('mute','unmute','notice') else 'notice')+'.wav')
    source=Path(path) if path and Path(path).is_file() else fallback
    try:
        prepared=prepared_file(source,volume,folder)
    except (OSError,ValueError,EOFError,wave.Error):
        if source==fallback:raise
        log.warning('Custom WAV unavailable or unsupported; using built-in %s',kind)
        prepared=prepared_file(fallback,volume,folder)
    with wave.open(str(prepared),'rb') as audio:
        params=audio.getparams()
        frames=audio.readframes(audio.getnframes())
    _player.play(params,frames,_reset)


def _run():
    global _player
    comtypes.CoInitializeEx(0)
    try:
        _player=WasapiOutput()
        _reset.clear()
        while True:
            request=_requests.get()
            if request is None:return
            if request is _RESET or _reset.is_set():
                _player.close();_reset.clear()
                if request is _RESET:continue
            try:_play(*request,Path(_cache.name))
            except (OSError,RuntimeError,ValueError,EOFError,wave.Error,comtypes.COMError):
                log.exception('Could not play sound')
                _player.close()
    finally:
        if _player is not None:_player.close()
        _player=None
        comtypes.CoUninitialize()


def reset_output():
    """Sleep/resume releases stale audio handles on their owning sound thread."""
    with _lock:
        if _closed or _worker is None:return
        _reset.set()
        try:_requests.get_nowait()
        except queue.Empty:pass
        _requests.put_nowait(_RESET)


def play(path='', kind='notice', volume=70):
    """Nonblocking for GUI/audio worker; finish current sound, then newest request."""
    global _worker,_cache
    volume=max(0,min(100,int(volume)))
    if not volume:return
    with _lock:
        if _closed:return
        if _worker is None:
            _cache=tempfile.TemporaryDirectory(prefix='SoundControl-audio-')
            _worker=threading.Thread(target=_run,name='SoundControl sounds',daemon=True)
            _worker.start()
        try:_requests.get_nowait()
        except queue.Empty:pass
        _requests.put_nowait((path,kind,volume))


def shutdown():
    global _closed
    with _lock:
        _closed=True
        if _worker is None:return
        _reset.set()
        try:_requests.get_nowait()
        except queue.Empty:pass
        _requests.put_nowait(None)
    _worker.join(timeout=2)
    if not _worker.is_alive():_cache.cleanup()


atexit.register(shutdown)
