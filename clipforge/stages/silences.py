"""Etapa: eliminación de silencios → tramos con voz de cada clip (en highlights.json)."""

from __future__ import annotations

import logging
from typing import Sequence

from clipforge.config import Config
from clipforge.job import Job
from clipforge.utils.timing import Word, flatten_words

log = logging.getLogger(__name__)
Segment = tuple[float, float]


# ---------- Lógica pura ----------

def keep_segments(words: Sequence[Word], duration: float, max_gap: float, pad: float,
                  min_segment: float = 0.2) -> list[Segment]:
    """Tramos a conservar (tiempos relativos al clip).

    Recorta el silencio inicial/final y toda pausa entre palabras mayor que `max_gap`,
    dejando `pad` segundos de margen a cada lado para que el corte no suene brusco.
    """
    if not words:
        return [(0.0, round(duration, 3))]
    segs: list[list[float]] = [[max(0.0, words[0]["s"] - pad), words[0]["e"]]]
    for prev, w in zip(words, words[1:]):
        if w["s"] - prev["e"] > max_gap:
            segs[-1][1] = prev["e"] + pad
            segs.append([max(segs[-1][1], w["s"] - pad), w["e"]])
        else:
            segs[-1][1] = max(segs[-1][1], w["e"])
    segs[-1][1] = min(duration, segs[-1][1] + pad)
    # Une tramos que se tocan y descarta los diminutos (ruidos aislados): unirlos al
    # anterior volvería a meter el silencio que los separa.
    merged: list[list[float]] = []
    for s in segs:
        if merged and s[0] - merged[-1][1] < 0.01:
            merged[-1][1] = s[1]
        elif s[1] - s[0] >= min_segment:
            merged.append(s)
    if not merged:
        return [(0.0, round(duration, 3))]
    return [(round(a, 3), round(min(b, duration), 3)) for a, b in merged if b > a]


def edited_duration(segments: Sequence[Segment]) -> float:
    return round(sum(b - a for a, b in segments), 3)


def remap(t: float, segments: Sequence[Segment]) -> float:
    """Tiempo del clip original → tiempo del clip ya sin silencios."""
    acc = 0.0
    for a, b in segments:
        if t < a:
            return acc  # cae en un hueco recortado: salta al inicio del siguiente tramo
        if t <= b:
            return acc + (t - a)
        acc += b - a
    return acc


def remap_words(words: Sequence[Word], segments: Sequence[Segment] | None) -> list[Word]:
    """Ajusta los tiempos de las palabras al clip recortado."""
    if not segments:
        return list(words)
    out = []
    for w in words:
        s, e = remap(w["s"], segments), remap(w["e"], segments)
        if e > s:
            out.append({"w": w["w"], "s": round(s, 3), "e": round(e, 3)})
    return out


# ---------- Etapa ----------

def enabled_for(clip: dict, job: Job, cfg: Config) -> bool:
    """Ajuste por clip (web) > opción del trabajo > config.yaml."""
    if clip.get("cut_silences") is not None:
        return bool(clip["cut_silences"])
    return bool(job.options.get("silences", cfg.silences.enabled))


def done(job: Job) -> bool:
    clips = job.load_json("highlights.json")["clips"] if job.exists("highlights.json") else []
    return bool(clips) and all("edited_duration" in c for c in clips)


def run_stage(job: Job, cfg: Config, only: int | None = None) -> None:
    from clipforge.stages.captions import clip_words

    sc = cfg.silences
    data = job.load_json("highlights.json")
    words = flatten_words(job.load_json("transcript.json"))
    for clip in data["clips"]:
        if only is not None and clip["n"] != only:
            continue
        duration = clip["end"] - clip["start"]
        if enabled_for(clip, job, cfg):
            segs = keep_segments(clip_words(words, clip["start"], clip["end"]), duration,
                                 sc.max_gap, sc.pad, sc.min_segment)
            clip["segments"] = [list(s) for s in segs]
            clip["edited_duration"] = edited_duration(segs)
        else:
            clip["segments"] = None
            clip["edited_duration"] = round(duration, 3)
        cut = duration - clip["edited_duration"]
        log.info("  clip %d: %.1f s → %.1f s (−%.1f s de silencios, %d cortes)", clip["n"], duration,
                 clip["edited_duration"], cut, max(0, len(clip["segments"] or [1]) - 1))
    job.save_json("highlights.json", data)
