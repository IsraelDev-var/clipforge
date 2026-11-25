"""Etapa: puntuación de viralidad por clip (criterios de la IA + señales medidas)."""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Sequence

import numpy as np

from clipforge.config import Config
from clipforge.job import Job
from clipforge.utils.timing import Word, flatten_words

log = logging.getLogger(__name__)

LLM_WEIGHT = 0.7  # el resto lo aportan las señales medidas
LLM_CRITERIA = {"hook": 0.30, "standalone": 0.20, "value": 0.25, "emotion": 0.10, "shareability": 0.15}
SIGNAL_WEIGHTS = {"hook_delay": 0.25, "pace": 0.20, "dead_air": 0.20, "ending": 0.15, "duration": 0.10, "energy": 0.10}
QUESTION_BONUS = 3
GAP = 0.7  # pausa (s) que cuenta como silencio
SENTENCE_END = (".", "?", "!", "…")


def _lerp01(x: float, bad: float, good: float) -> float:
    """0 en `bad`, 1 en `good`, lineal entre medias (sirve en ambos sentidos)."""
    if bad == good:
        return 1.0
    return float(min(1.0, max(0.0, (x - bad) / (good - bad))))


def grade(score: int) -> str:
    return "alto" if score >= 80 else "medio" if score >= 60 else "bajo"


# ---------- Señales (lógica pura) ----------

def speech_signals(words: Sequence[Word], duration: float) -> dict[str, float]:
    """Métricas del habla de un clip; `words` con tiempos relativos al inicio del clip."""
    if not words or duration <= 0:
        return {"hook_delay": duration, "pace": 0.0, "dead_air": 1.0, "ends_sentence": 0.0, "question_hook": 0.0}
    silence = max(0.0, words[0]["s"]) + max(0.0, duration - words[-1]["e"])
    silence += sum(b["s"] - a["e"] for a, b in zip(words, words[1:]) if b["s"] - a["e"] > GAP)
    first = " ".join(w["w"] for w in words if w["s"] < 3.5)
    return {
        "hook_delay": round(words[0]["s"], 2),
        "pace": round(len(words) / duration, 2),
        "dead_air": round(min(1.0, silence / duration), 3),
        "ends_sentence": float(words[-1]["w"].endswith(SENTENCE_END)),
        "question_hook": float("?" in first or "¿" in first),
    }


def energy_dynamics(samples: np.ndarray, rate: int, window: float = 0.5) -> float:
    """Coeficiente de variación del volumen (RMS por ventanas): voz plana ≈ 0.3, enérgica ≥ 0.8."""
    n = int(rate * window)
    if samples.size < n * 4:
        return 0.0
    frames = samples[: samples.size // n * n].astype(np.float32).reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    rms = rms[rms > rms.max() * 0.05]  # ignora silencio total
    if rms.size < 4 or rms.mean() == 0:
        return 0.0
    return round(float(rms.std() / rms.mean()), 3)


def signal_subscores(sig: dict[str, float], duration: float) -> dict[str, float]:
    """Cada señal a 0-1 (1 = mejor)."""
    pace = sig["pace"]
    return {
        "hook_delay": _lerp01(sig["hook_delay"], 2.0, 0.3),
        "pace": _lerp01(pace, 1.2, 2.3) if pace < 2.3 else _lerp01(pace, 5.0, 3.8),
        "dead_air": _lerp01(sig["dead_air"], 0.30, 0.05),
        "ending": 1.0 if sig["ends_sentence"] else 0.4,
        "duration": 1.0 if 20 <= duration <= 45 else (_lerp01(duration, 8, 20) if duration < 20
                                                       else max(0.4, _lerp01(duration, 90, 45))),
        "energy": _lerp01(sig.get("energy", 0.0), 0.25, 0.85),
    }


def tips(sig: dict[str, float], sub: dict[str, float], llm: dict[str, int] | None, duration: float) -> list[str]:
    """Consejos accionables, de mayor a menor impacto."""
    out: list[str] = []
    if sub["hook_delay"] < 0.6:
        out.append(f"La primera palabra llega a los {sig['hook_delay']:.1f} s: recorta el inicio para entrar directo al gancho.")
    if llm and llm.get("hook", 10) <= 5:
        out.append("Gancho débil según la IA: empieza en la frase más llamativa o con una pregunta.")
    if sub["dead_air"] < 0.6:
        out.append(f"{sig['dead_air'] * 100:.0f} % del clip es silencio: elimina pausas para mantener el ritmo.")
    if sub["pace"] < 0.6:
        slow = sig["pace"] < 2.3
        out.append(f"Ritmo {'lento' if slow else 'muy rápido'} ({sig['pace']:.1f} palabras/s)"
                   + (": busca un tramo con más dinamismo." if slow else ": puede costar seguir los subtítulos."))
    if not sig["ends_sentence"]:
        out.append("No termina en fin de frase: alarga o recorta el final para cerrar la idea.")
    if llm and llm.get("standalone", 10) <= 5:
        out.append("Necesita contexto para entenderse: añade al inicio la frase que plantea el problema.")
    if sub["duration"] < 1.0:
        out.append(f"Dura {duration:.0f} s: los clips de 20–45 s suelen retener mejor.")
    if sub["energy"] < 0.3:
        out.append("Entonación plana: los momentos con más energía o reacción funcionan mejor.")
    return out


def compute(clip: dict, words: Sequence[Word], energy: float, duration: float | None = None) -> dict:
    """Puntuación final 0-100 con desglose y consejos.

    `words` relativas al clip final (ya sin silencios, si se recortaron) y `duration` la del clip final.
    """
    duration = duration if duration is not None else clip["end"] - clip["start"]
    sig = speech_signals(words, duration) | {"energy": energy}
    sub = signal_subscores(sig, duration)
    signals_score = 100 * sum(sub[k] * w for k, w in SIGNAL_WEIGHTS.items())

    llm = clip.get("virality_llm")
    if llm:
        llm_score = 10 * sum(llm[k] * w for k, w in LLM_CRITERIA.items())
    else:  # clips antiguos sin criterios: se usa la puntuación general de la IA
        llm_score = float(clip.get("score") or 50)

    score = LLM_WEIGHT * llm_score + (1 - LLM_WEIGHT) * signals_score
    if sig["question_hook"]:
        score += QUESTION_BONUS
    score = int(round(min(100, max(1, score))))
    return {
        "score": score,
        "grade": grade(score),
        "llm": llm,
        "llm_score": round(llm_score),
        "signals_score": round(signals_score),
        "signals": sig,
        "subscores": {k: round(v, 2) for k, v in sub.items()},
        "tips": tips(sig, sub, llm, duration),
    }


# ---------- Etapa ----------

def _read_audio(path: Path, start: float, end: float) -> tuple[np.ndarray, int]:
    """Lee solo el tramo [start, end] del WAV mono 16-bit."""
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        w.setpos(min(w.getnframes(), int(start * rate)))
        data = w.readframes(max(0, int((end - start) * rate)))
        return np.frombuffer(data, dtype=np.int16), rate


def done(job: Job) -> bool:
    clips = job.load_json("highlights.json")["clips"] if job.exists("highlights.json") else []
    return bool(clips) and all(isinstance(c.get("virality"), dict) and "score" in c["virality"] for c in clips)


def run_stage(job: Job, cfg: Config, only: int | None = None) -> None:
    from clipforge.stages.captions import clip_words
    from clipforge.stages.silences import remap_words

    data = job.load_json("highlights.json")
    words = flatten_words(job.load_json("transcript.json"))
    audio = job.path("audio.wav")
    for clip in data["clips"]:
        if only is not None and clip["n"] != only:
            continue
        # Los criterios de la IA vienen de select; se guardan aparte para poder recalcular.
        if "virality_llm" not in clip:
            v = clip.get("virality")
            clip["virality_llm"] = v if isinstance(v, dict) and "hook" in v else None
        energy = energy_dynamics(*_read_audio(audio, clip["start"], clip["end"])) if audio.exists() else 0.0
        cw = remap_words(clip_words(words, clip["start"], clip["end"]), clip.get("segments"))
        clip["virality"] = compute(clip, cw, energy, clip.get("edited_duration"))
        log.info("  clip %d: viralidad %d (%s)", clip["n"], clip["virality"]["score"], clip["virality"]["grade"])
    job.save_json("highlights.json", data)
