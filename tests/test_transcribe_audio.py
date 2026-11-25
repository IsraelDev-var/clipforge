import wave
from types import SimpleNamespace

import numpy as np
import pytest

from clipforge import ClipForgeError
from clipforge.config import Config
from clipforge.stages import transcribe
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


class _FakeModel:
    def __init__(self, fail: bool):
        self.fail = fail

    def transcribe(self, audio, **kw):
        def gen():
            if self.fail:
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            word = SimpleNamespace(word=" hola", start=0.0, end=0.5)
            yield SimpleNamespace(start=0.0, end=0.5, text=" hola", words=[word])
        return gen(), SimpleNamespace(duration=1.0, language="es")


class _FakeJob:
    def __init__(self, tmp_path):
        self.dir, self.saved = tmp_path, {}

    def path(self, name):
        return self.dir / name

    def save_json(self, name, data):
        self.saved[name] = data


def test_falls_back_to_cpu_when_cuda_dlls_missing(tmp_path, monkeypatch):
    job = _FakeJob(tmp_path)
    _write(job.path("audio.wav"), [0] * 160)
    devices = []

    def fake_load(cfg, device=None):
        device = device or "cuda"
        devices.append(device)
        return _FakeModel(fail=device == "cuda"), device

    monkeypatch.setattr(transcribe, "_load_model", fake_load)
    transcribe.run_stage(job, Config())
    assert devices == ["cuda", "cpu"]
    assert job.saved["transcript.json"]["segments"][0]["text"] == "hola"


def test_other_runtime_errors_are_not_swallowed(tmp_path, monkeypatch):
    job = _FakeJob(tmp_path)
    _write(job.path("audio.wav"), [0] * 160)

    class Boom(_FakeModel):
        def transcribe(self, audio, **kw):
            raise RuntimeError("otra cosa")

    monkeypatch.setattr(transcribe, "_load_model", lambda cfg, device=None: (Boom(False), "cuda"))
    with pytest.raises(RuntimeError, match="otra cosa"):
        transcribe.run_stage(job, Config())
