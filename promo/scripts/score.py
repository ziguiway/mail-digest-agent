"""Render the original, subdued score used in the homepage cut."""
import math
from array import array
from pathlib import Path
import subprocess

RATE = 24_000
SECONDS = 34
ROOT = Path(__file__).resolve().parents[1] / 'public' / 'audio'
CHORDS = ((48, 55, 64, 67), (45, 52, 60, 67), (41, 53, 60, 64),
          (43, 55, 62, 69), (48, 55, 64, 71))
CUES = (0, 6.8, 13.6, 20.4, 27.2)

def frequency(note):
    return 440 * 2 ** ((note - 69) / 12)

layers = [[(frequency(note), i) for i, note in enumerate(chord)] for chord in CHORDS]
samples = array('h')
for sample in range(RATE * SECONDS):
    t = sample / RATE
    left = right = 0.
    for cue, layer in zip(CUES, layers):
        elapsed = t - cue
        if not 0 <= elapsed < 8.6:
            continue
        fade = min(1., elapsed / 1.5, (8.6 - elapsed) / 1.8)
        for hz, i in layer:
            phase = math.tau * hz * elapsed
            tone = (math.sin(phase) + .17 * math.sin(2 * phase + .2 * math.sin(elapsed))) * .033
            if i > 0 and elapsed > .18 + i * .46:
                hit = elapsed - (.18 + i * .46)
                attack = min(1., hit / .15) * math.exp(-hit * 1.4)
                tone += .047 * attack * (math.sin(2 * phase) + .20 * math.sin(3 * phase))
            pan = (.58, .24, .78, .38)[i]
            left += fade * tone * (1. - pan)
            right += fade * tone * pan
    tail = min(1., (SECONDS - t) / 2.0)
    samples.extend((round(32767 * left * tail), round(32767 * right * tail)))

subprocess.run([
    'ffmpeg', '-v', 'error', '-y', '-f', 's16le', '-ar', str(RATE), '-ac', '2',
    '-i', 'pipe:0', '-af', 'volume=6dB', '-b:a', '128k', str(ROOT / 'laixin-score.mp3'),
], input=samples.tobytes(), check=True)
