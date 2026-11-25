"""Etapa: extrae audio mono 16 kHz → audio.wav."""

from __future__ import annotations

import logging

from clipforge.config import Config
from clipforge.job import Job
from clipforge.utils.ffmpeg import run

log = logging.getLogger(__name__)
OUTPUT = "audio.wav"


def done(job: Job) -> bool:
    return job.exists(OUTPUT)


def run_stage(job: Job, cfg: Config) -> None:
    tmp = job.path("audio.part.wav")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(job.path("source.mp4")),
         "-vn", "-ac", "1", "-ar", "16000", str(tmp)])
    tmp.replace(job.path(OUTPUT))
    log.info("Audio extraído")
