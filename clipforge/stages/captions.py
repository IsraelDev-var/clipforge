"""Etapa 6: subtítulos estilo Shorts con palabra activa resaltada → subs_<n>.ass."""

from __future__ import annotations

import json
import logging
from typing import Sequence

from clipforge.config import CaptionsConfig, Config
from clipforge.job import Job
from clipforge.stages.silences import remap_words
from clipforge.utils.timing import Word, ass_time, flatten_words

log = logging.getLogger(__name__)
PUNCT_BREAK = (".", "?", "!", "…", ",", ":", ";")
MAX_GAP = 0.6       # silencio que fuerza un bloque nuevo
HOLD_GAP = 0.4      # huecos menores entre bloques se rellenan para evitar parpadeos


def subs_name(n: int) -> str:
    return f"subs_{n}.ass"


def clip_words(words: Sequence[Word], start: float, end: float) -> list[Word]:
    """Palabras dentro del clip con tiempos relativos a su inicio."""
    out = []
    for w in words:
        if w["e"] <= start or w["s"] >= end:
            continue
        out.append({"w": w["w"], "s": round(max(0.0, w["s"] - start), 3), "e": round(min(end, w["e"]) - start, 3)})
    return out


def group_words(words: Sequence[Word], per_chunk: int, max_chars: int) -> list[list[Word]]:
    """Agrupa en bloques de hasta `per_chunk` palabras y `max_chars` caracteres.

    Corta también tras puntuación o en silencios largos.
    """
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            length = len(" ".join(x["w"] for x in cur + [w]))
            if (len(cur) >= per_chunk or length > max_chars or w["s"] - cur[-1]["e"] > MAX_GAP
                    or cur[-1]["w"].endswith(PUNCT_BREAK)):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def _inline_color(ass_color: str) -> str:
    """`&H00BBGGRR` (estilo) → `&HBBGGRR&` (tag en línea)."""
    hexpart = ass_color.upper().replace("&H", "").replace("&", "").rjust(8, "0")
    return f"&H{hexpart[-6:]}&"


def build_events(chunks: list[list[Word]], cc: CaptionsConfig) -> list[tuple[float, float, str]]:
    """Un evento por palabra: muestra el bloque completo con la palabra activa en color."""
    hl = _inline_color(cc.highlight_color)
    events: list[tuple[float, float, str]] = []
    for ci, chunk in enumerate(chunks):
        chunk_end = chunk[-1]["e"]
        if ci + 1 < len(chunks) and chunks[ci + 1][0]["s"] - chunk_end < HOLD_GAP:
            chunk_end = chunks[ci + 1][0]["s"]
        texts = [_esc(w["w"].upper() if cc.uppercase else w["w"]) for w in chunk]
        for wi, w in enumerate(chunk):
            start = w["s"]
            end = chunk[wi + 1]["s"] if wi + 1 < len(chunk) else chunk_end
            if end <= start:
                continue
            line = " ".join(f"{{\\c{hl}}}{t}{{\\r}}" if j == wi else t for j, t in enumerate(texts))
            events.append((start, end, line))
    return events


def margin_for_layout(mode: str, cfg: Config) -> int:
    """Margen inferior de los subtítulos según el diseño del clip."""
    h = cfg.output_resolution[1]
    center = {"split": cfg.layout.captions_split_y, "screen": cfg.layout.captions_screen_y}.get(mode)
    if center is None:
        return cfg.captions.margin_v
    # centro del texto en `center`: el margen se mide desde abajo hasta la base del texto
    return int(h - center * h - cfg.captions.size / 2)


def build_ass(words: Sequence[Word], cc: CaptionsConfig, resolution: tuple[int, int],
              margin_v: int | None = None) -> str:
    """Documento .ass completo para un clip (tiempos ya relativos)."""
    w, h = resolution
    margin_v = cc.margin_v if margin_v is None else margin_v
    chunks = group_words(words, cc.words_per_chunk, cc.max_chars)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cc.font},{cc.size},{cc.primary_color},{cc.highlight_color},{cc.outline_color},&H80000000,-1,0,0,0,100,100,0,0,1,{cc.outline},{cc.shadow},2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Default,,0,0,0,,{t}" for s, e, t in build_events(chunks, cc)]
    return header + "\n".join(lines) + "\n"


def done(job: Job) -> bool:
    clips = job.load_json("highlights.json")["clips"] if job.exists("highlights.json") else []
    return bool(clips) and all(job.exists(subs_name(c["n"])) for c in clips)


def run_stage(job: Job, cfg: Config, only: int | None = None) -> None:
    words = flatten_words(job.load_json("transcript.json"))
    for clip in job.load_json("highlights.json")["clips"]:
        if only is not None and clip["n"] != only:
            continue
        cw = remap_words(clip_words(words, clip["start"], clip["end"]), clip.get("segments"))
        track = job.path(f"crop_track_{clip['n']}.json")
        mode = json.loads(track.read_text(encoding="utf-8"))["mode"] if track.exists() else "face"
        ass = build_ass(cw, cfg.captions, cfg.output_resolution, margin_for_layout(mode, cfg))
        job.path(subs_name(clip["n"])).write_text(ass, encoding="utf-8")
    log.info("Subtítulos generados")
