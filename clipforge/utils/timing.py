"""Utilidades de tiempo: formato ASS y ajuste a límites de palabra."""

from __future__ import annotations

import bisect
from typing import Sequence, TypedDict


class Word(TypedDict):
    w: str
    s: float
    e: float


def ass_time(seconds: float) -> str:
    """Formatea segundos como `H:MM:SS.cc` (centésimas) para ASS."""
    cs = max(0, round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def hms(seconds: float) -> str:
    """Formato legible `HH:MM:SS`."""
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _nearest(values: Sequence[float], t: float) -> int:
    i = bisect.bisect_left(values, t)
    if i == 0:
        return 0
    if i == len(values):
        return len(values) - 1
    return i if values[i] - t < t - values[i - 1] else i - 1


def snap_start(t: float, words: Sequence[Word]) -> int:
    """Índice de la palabra cuyo inicio está más cerca de `t`."""
    return _nearest([w["s"] for w in words], t)


def snap_end(t: float, words: Sequence[Word]) -> int:
    """Índice de la palabra cuyo final está más cerca de `t`."""
    return _nearest([w["e"] for w in words], t)


def flatten_words(transcript: dict) -> list[Word]:
    """Lista ordenada de todas las palabras de un transcript.json."""
    words = [w for seg in transcript["segments"] for w in seg.get("words", [])]
    words.sort(key=lambda w: w["s"])
    return words
