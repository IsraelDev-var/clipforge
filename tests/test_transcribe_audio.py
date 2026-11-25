import wave

import numpy as np
import pytest

from clipforge import ClipForgeError
from clipforge.stages.transcribe import load_wav


def _write(path, samples, rate=16000, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def test_load_wav_float32(tmp_path):
    p = tmp_path / "audio.wav"
    _write(p, [0, 16384, -32768, 32767])
    audio = load_wav(p)
    assert audio.dtype == np.float32
    assert audio.tolist() == pytest.approx([0.0, 0.5, -1.0, 32767 / 32768])


def test_load_wav_rejects_other_rates(tmp_path):
    p = tmp_path / "audio.wav"
    _write(p, [0] * 10, rate=44100)
    with pytest.raises(ClipForgeError, match="--from audio"):
        load_wav(p)
