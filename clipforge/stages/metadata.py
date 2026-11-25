"""Etapa 8: clips.json con la información de publicación de cada clip."""

from __future__ import annotations

import logging

from clipforge.config import Config
from clipforge.job import Job
from clipforge.stages.render import clip_name

log = logging.getLogger(__name__)
OUTPUT = "clips.json"


def done(job: Job) -> bool:
    return job.exists(OUTPUT)


def run_stage(job: Job, cfg: Config) -> None:
    clips = [
        {
            "n": c["n"],
            "file": clip_name(c["n"]),
            "start": c["start"],
            "end": c["end"],
            "duration": c.get("edited_duration", round(c["end"] - c["start"], 3)),
            "source_duration": round(c["end"] - c["start"], 3),
            "silence_removed": round(c["end"] - c["start"] - c.get("edited_duration", c["end"] - c["start"]), 3),
            "score": c.get("score"),
            "virality": (c.get("virality") or {}).get("score"),
            "virality_grade": (c.get("virality") or {}).get("grade"),
            "virality_breakdown": {"ia": c.get("virality_llm"), "señales": (c.get("virality") or {}).get("subscores")},
            "tips": (c.get("virality") or {}).get("tips", []),
            "title": c.get("title", ""),
            "description": c.get("description", ""),
            "hashtags": c.get("hashtags", []),
            "hook": c.get("hook", ""),
            "reason": c.get("reason", ""),
        }
        for c in job.load_json("highlights.json")["clips"]
    ]
    job.save_json(OUTPUT, {"job_id": job.id, "source": job.meta.get("source"), "clips": clips})
    log.info("Metadatos en %s", job.path(OUTPUT))
