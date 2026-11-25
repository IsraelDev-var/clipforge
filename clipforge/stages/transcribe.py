"""Etapa: transcripción con faster-whisper → transcript.json."""

from __future__ import annotations

import logging
import time

from clipforge import ClipForgeError
from clipforge.config import Config
from clipforge.job import Job
from clipforge.utils.timing import hms

log = logging.getLogger(__name__)
OUTPUT = "transcript.json"


def done(job: Job) -> bool:
    return job.exists(OUTPUT)


CUDA_ERRORS = ("cublas", "cudnn", "cuda", "cudart")


def _add_cuda_dlls() -> None:
    """Hace visibles las DLL de CUDA instaladas con pip (extra `gpu`: nvidia-cublas-cu12, nvidia-cudnn-cu12)."""
    import importlib.util
    import os
    import sys
    from pathlib import Path

    if sys.platform != "win32":
        return
    spec = importlib.util.find_spec("nvidia")
    if not spec or not spec.submodule_search_locations:
        return
    for root in spec.submodule_search_locations:
        for bin_dir in Path(root).glob("*/bin"):
            if str(bin_dir) not in os.environ.get("PATH", ""):
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def _is_cuda_error(exc: Exception) -> bool:
    return any(k in str(exc).lower() for k in CUDA_ERRORS)


def _load_model(cfg: Config, device: str | None = None):
    """Devuelve (modelo, dispositivo)."""
    _add_cuda_dlls()
    from faster_whisper import WhisperModel

    device = device or cfg.whisper_device
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:  # noqa: BLE001
            device = "cpu"
    compute = cfg.whisper_compute_type
    if compute == "auto" or (device == "cpu" and compute == "float16"):
        compute = "float16" if device == "cuda" else "int8"
    log.info("Cargando Whisper «%s» en %s (%s)…", cfg.whisper_model, device, compute)
    try:
        return WhisperModel(cfg.whisper_model, device=device, compute_type=compute), device
    except Exception as exc:  # noqa: BLE001
        if device == "cuda" and _is_cuda_error(exc):
            log.warning("La GPU no está disponible (%s); uso la CPU, que es más lenta.", exc)
            return _load_model(cfg, "cpu")
        raise ClipForgeError(f"No se pudo cargar el modelo Whisper «{cfg.whisper_model}»: {exc}\n"
                             "La primera vez se descarga de huggingface.co: revisa tu conexión.") from exc


def load_wav(path) -> "np.ndarray":
    """Lee el audio.wav (PCM 16 bits, mono, 16 kHz) como float32 en [-1, 1].

    Se decodifica aquí en vez de dejarlo a faster-whisper, que usa PyAV y falla con
    algunas versiones (p. ej. «open() got an unexpected keyword argument 'metadata_errors'»).
    """
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
            raise ClipForgeError(f"audio.wav con formato inesperado ({w.getnchannels()} canales, "
                                 f"{w.getframerate()} Hz, {8 * w.getsampwidth()} bits). "
                                 "Regenéralo con `clipforge resume <job> --from audio`.")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def _transcribe(model, audio, cfg: Config) -> tuple[list[dict], object, float]:
    segments, info = model.transcribe(
        audio,
        language=cfg.language,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=cfg.initial_prompt,
        beam_size=5,
    )
    total = info.duration or 1
    out_segments: list[dict] = []
    last_log = 0.0
    for seg in segments:  # generador: la transcripción ocurre al iterar
        words = [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)}
                 for w in (seg.words or []) if w.word.strip()]
        out_segments.append({"id": len(out_segments), "start": round(seg.start, 3), "end": round(seg.end, 3),
                             "text": seg.text.strip(), "words": words})
        if time.time() - last_log > 10:
            last_log = time.time()
            log.info("Transcribiendo… %s / %s (%.0f%%)", hms(seg.end), hms(total), 100 * seg.end / total)
    return out_segments, info, total


def run_stage(job: Job, cfg: Config) -> None:
    audio = load_wav(job.path("audio.wav"))
    model, device = _load_model(cfg)
    t0 = time.time()
    try:
        out_segments, info, total = _transcribe(model, audio, cfg)
    except RuntimeError as exc:
        # Las DLL de CUDA solo se cargan al transcribir: si faltan, se repite en CPU.
        if device != "cuda" or not _is_cuda_error(exc):
            raise
        log.warning("Fallo de la GPU (%s); repito la transcripción en CPU (más lenta). "
                    'Para usar la GPU: pip install -e ".[gpu]"', exc)
        del model
        model, device = _load_model(cfg, "cpu")
        out_segments, info, total = _transcribe(model, audio, cfg)
    if not out_segments:
        raise ClipForgeError("La transcripción salió vacía (¿el video no tiene voz?).")
    job.save_json(OUTPUT, {"language": info.language, "duration": round(total, 3), "segments": out_segments})
    log.info("Transcripción lista (%s): %d segmentos en %.0f s", device, len(out_segments), time.time() - t0)
