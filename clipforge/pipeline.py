"""Orquesta las etapas y la reanudación."""

from __future__ import annotations

import logging
import time
from types import ModuleType

from clipforge import ClipForgeError
from clipforge.config import Config
from clipforge.job import Job
from clipforge.stages import audio, captions, ingest, metadata, reframe, render, select, transcribe
from clipforge.utils.ffmpeg import require_ffmpeg

log = logging.getLogger(__name__)

STAGES: dict[str, ModuleType] = {
    "ingest": ingest,
    "audio": audio,
    "transcribe": transcribe,
    "select": select,
    "reframe": reframe,
    "captions": captions,
    "render": render,
    "metadata": metadata,
}
# Etapas que dependen de highlights.json: si se re-selecciona hay que rehacerlas.
_PER_CLIP = ("reframe", "captions", "render", "metadata")


def run_pipeline(job: Job, cfg: Config, from_stage: str | None = None) -> None:
    """Ejecuta las etapas en orden. Salta las ya hechas, salvo `from_stage` y las siguientes."""
    require_ffmpeg()
    if from_stage is not None and from_stage not in STAGES:
        raise ClipForgeError(f"Etapa desconocida «{from_stage}». Opciones: {', '.join(STAGES)}")
    names = list(STAGES)
    force_from = names.index(from_stage) if from_stage else len(names)
    t_total = time.time()
    for i, name in enumerate(names):
        stage = STAGES[name]
        if i < force_from and stage.done(job):
            log.info("[%d/%d] %s: ya hecho, se omite", i + 1, len(names), name)
            continue
        log.info("[%d/%d] %s…", i + 1, len(names), name)
        t0 = time.time()
        stage.run_stage(job, cfg)
        log.info("[%d/%d] %s listo (%.1f s)", i + 1, len(names), name, time.time() - t0)
        if name == "select":
            force_from = min(force_from, names.index("reframe"))  # nuevos clips → rehacer el resto
    log.info("Trabajo %s completado en %.0f s → %s", job.id, time.time() - t_total, job.dir)


def rerender_clip(job: Job, cfg: Config, n: int, start: float | None, end: float | None) -> None:
    """Ajuste manual de un clip: actualiza inicio/fin y rehace reframe, subtítulos, render y metadata."""
    require_ffmpeg()
    data = job.load_json("highlights.json")
    clip = next((c for c in data["clips"] if c["n"] == n), None)
    if clip is None:
        raise ClipForgeError(f"El clip {n} no existe. Clips disponibles: {[c['n'] for c in data['clips']]}")
    duration = job.meta.get("video", {}).get("duration")
    clip["start"] = clip["start"] if start is None else max(0.0, start)
    clip["end"] = clip["end"] if end is None else (min(end, duration) if duration else end)
    if clip["end"] <= clip["start"]:
        raise ClipForgeError("El final del clip debe ser posterior al inicio.")
    clip["duration"] = round(clip["end"] - clip["start"], 3)
    job.save_json("highlights.json", data)
    for name in _PER_CLIP[:-1]:
        log.info("%s (clip %d)…", name, n)
        STAGES[name].run_stage(job, cfg, only=n)
    metadata.run_stage(job, cfg)
    log.info("Clip %d listo: %s", n, job.path(render.clip_name(n)))
