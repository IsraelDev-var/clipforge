"""Etapa 3: transcripción con faster-whisper → transcript.json."""

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


def _load_model(cfg: Config):
    from faster_whisper import WhisperModel

    device = cfg.whisper_device
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:  # noqa: BLE001
            device = "cpu"
    compute = cfg.whisper_compute_type
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    log.info("Cargando Whisper «%s» en %s (%s)…", cfg.whisper_model, device, compute)
    try:
        return WhisperModel(cfg.whisper_model, device=device, compute_type=compute)
    except Exception as exc:  # noqa: BLE001
        raise ClipForgeError(f"No se pudo cargar el modelo Whisper «{cfg.whisper_model}»: {exc}") from exc


def run_stage(job: Job, cfg: Config) -> None:
    model = _load_model(cfg)
    t0 = time.time()
    segments, info = model.transcribe(
        str(job.path("audio.wav")),
        language=cfg.language,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=cfg.initial_prompt,
        beam_size=5,
    )
    total = info.duration or 1
    out_segments = []
    last_log = 0.0
    for seg in segments:  # generador: la transcripción ocurre al iterar
        words = [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)}
                 for w in (seg.words or []) if w.word.strip()]
        out_segments.append({"id": len(out_segments), "start": round(seg.start, 3), "end": round(seg.end, 3),
                             "text": seg.text.strip(), "words": words})
        if time.time() - last_log > 10:
            last_log = time.time()
            log.info("Transcribiendo… %s / %s (%.0f%%)", hms(seg.end), hms(total), 100 * seg.end / total)
    if not out_segments:
        raise ClipForgeError("La transcripción salió vacía (¿el video no tiene voz?).")
    job.save_json(OUTPUT, {"language": info.language, "duration": round(total, 3), "segments": out_segments})
    log.info("Transcripción lista: %d segmentos en %.0f s", len(out_segments), time.time() - t0)
