"""Original Sound Control earcons. Deterministic synthesis; no external samples."""
import math
from array import array
from pathlib import Path
import sys
import wave

RATE = 48000


def synthesize(kind):
    duration = {'mute':.24, 'unmute':.24, 'notice':.32}[kind]
    samples = array('h')
    notes = (880.0,659.255) if kind=='mute' else (659.255,880.0)
    for index in range(round(duration*RATE)):
        t = index/RATE
        if kind == 'notice':
            local = t if t < .14 else t-.17
            if local < 0 or local > .13:
                value = 0.0
            else:
                frequency = 920 if t < .14 else 1230
                envelope = min(1,local/.004)*math.exp(-local*30)*min(1,(.13-local)/.012)
                value = envelope*(math.sin(2*math.pi*frequency*local)+.22*math.sin(2*math.pi*frequency*2.01*local))
        else:
            # Two steady, pure notes with a short gap. No sweep, detuning or
            # beating partials; cosine ramps meet silence with zero slope.
            second = t >= .115
            local = t-.115 if second else t
            length = .105 if second else .09
            if local >= length:
                value = 0.0
            else:
                attack = .5-.5*math.cos(math.pi*min(1,local/.008))
                release = .5-.5*math.cos(math.pi*min(1,(length-local)/.025))
                level = (1.0 if second else .85) if kind=='unmute' else ( .78 if second else 1.0)
                value = level*attack*release*math.exp(-local*4)*math.sin(2*math.pi*notes[int(second)]*local)
        samples.append(round(max(-1,min(1,value*.34))*32767))
    if sys.byteorder != 'little':samples.byteswap()
    return samples.tobytes()


def generate(destination=None):
    folder = Path(destination) if destination else Path(__file__).with_name('audio_assets')
    folder.mkdir(parents=True,exist_ok=True)
    for kind in ('mute','unmute','notice'):
        with wave.open(str(folder/(kind+'.wav')),'wb') as output:
            output.setparams((1,2,RATE,0,'NONE','not compressed'))
            output.writeframes(synthesize(kind))


if __name__=='__main__':generate()
