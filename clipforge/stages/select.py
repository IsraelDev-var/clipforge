"""Etapa 4: el LLM elige los mejores momentos → highlights.json."""

from __future__ import annotations

import json
import logging
from importlib import resources
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, Field, ValidationError, field_validator

from clipforge import ClipForgeError
from clipforge.config import Config, Secrets
from clipforge.job import Job
from clipforge.utils.timing import Word, flatten_words, hms, snap_end, snap_start

log = logging.getLogger(__name__)
OUTPUT = "highlights.json"
MAX_RETRIES = 2
SENTENCE_END = (".", "?", "!", "…")


# ---------- Esquema ----------

CRITERIA = ("hook", "standalone", "value", "emotion", "shareability")


class ViralityLLM(BaseModel):
    """Valoración 1-10 de la IA para cada criterio de viralidad."""

    hook: int = 5
    standalone: int = 5
    value: int = 5
    emotion: int = 5
    shareability: int = 5

    @field_validator(*CRITERIA, mode="before")
    @classmethod
    def _clamp10(cls, v: Any) -> int:
        return max(1, min(10, int(round(float(v)))))


class Candidate(BaseModel):
    start: float
    end: float
    score: int = Field(default=50)
    hook: str = ""
    title: str = ""
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    reason: str = ""
    virality: ViralityLLM | None = None

    @field_validator("score", mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> int:
        return max(1, min(100, int(round(float(v)))))

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = v.strip()
        return v if len(v) <= 60 else v[:59].rstrip() + "…"

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return ["#" + t.strip().lstrip("#").replace(" ", "") for t in v if t.strip().lstrip("#")]


class ClipsResponse(BaseModel):
    clips: list[Candidate]


_STR = {"type": "string"}
CLIPS_SCHEMA = {
    "type": "object",
    "properties": {"clips": {"type": "array", "items": {
        "type": "object",
        "properties": {"start": {"type": "number"}, "end": {"type": "number"}, "score": {"type": "integer"},
                       "hook": _STR, "title": _STR, "description": _STR,
                       "hashtags": {"type": "array", "items": _STR}, "reason": _STR,
                       "virality": {"type": "object",
                                    "properties": {k: {"type": "integer"} for k in CRITERIA},
                                    "required": list(CRITERIA), "additionalProperties": False}},
        "required": ["start", "end", "score", "hook", "title", "description", "hashtags", "reason", "virality"],
        "additionalProperties": False,
    }}},
    "required": ["clips"],
    "additionalProperties": False,
}
RANK_SCHEMA = {
    "type": "object",
    "properties": {"selected": {"type": "array", "items": {"type": "integer"}}},
    "required": ["selected"],
    "additionalProperties": False,
}


# ---------- LLM ----------

class LLM(Protocol):
    def complete(self, system: str, user: str, schema: dict) -> str: ...


class ClaudeLLM:
    """Cliente mínimo de la API de Claude que devuelve el texto JSON de la respuesta."""

    def __init__(self, model: str, api_key: str | None = None) -> None:
        import anthropic

        self._anthropic = anthropic
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def complete(self, system: str, user: str, schema: dict) -> str:
        a = self._anthropic
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
                # Si el modelo rechaza la petición por seguridad, la API la reintenta con otro modelo.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.AuthenticationError as exc:
            raise ClipForgeError("Clave de API de Anthropic inválida o ausente. Define ANTHROPIC_API_KEY en .env.") from exc
        except a.RateLimitError as exc:
            raise ClipForgeError("Límite de peticiones de la API alcanzado. Espera un momento y usa "
                                 "`clipforge resume <job> --from select`.") from exc
        except a.APIStatusError as exc:
            raise ClipForgeError(f"Error de la API de Claude ({exc.status_code}): {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise ClipForgeError(f"No se pudo conectar con la API de Claude: {exc}") from exc
        if resp.stop_reason == "refusal":
            raise ClipForgeError("El modelo rechazó analizar este bloque de la transcripción.")
        return "".join(b.text for b in resp.content if b.type == "text")


def make_llm(cfg: Config) -> LLM:
    secrets = Secrets()
    return ClaudeLLM(cfg.llm_model, secrets.anthropic_api_key)


def _ask_clips(llm: LLM, system: str, user: str) -> list[Candidate]:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        text = llm.complete(system, user, CLIPS_SCHEMA)
        try:
            return ClipsResponse.model_validate_json(_strip_fences(text)).clips
        except ValidationError as exc:
            last_err = exc
            log.warning("JSON inválido del LLM (intento %d/%d)", attempt + 1, MAX_RETRIES + 1)
    raise ClipForgeError(f"El LLM devolvió JSON inválido tras {MAX_RETRIES + 1} intentos: {last_err}")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


# ---------- Transcripción → bloques ----------

def format_segments(segments: Sequence[dict]) -> str:
    return "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)


def chunk_segments(segments: Sequence[dict], chunk_s: float, overlap_s: float) -> list[list[dict]]:
    """Divide en bloques de `chunk_s` segundos con `overlap_s` de solapamiento."""
    if not segments:
        return []
    end_total = segments[-1]["end"]
    step = max(1.0, chunk_s - overlap_s)
    chunks, t = [], segments[0]["start"]
    while t < end_total:
        block = [s for s in segments if s["end"] > t and s["start"] < t + chunk_s]
        if block:
            chunks.append(block)
        if t + chunk_s >= end_total:
            break
        t += step
    return chunks


# ---------- Post-procesado (lógica pura, con tests) ----------

def _fit_end(start_idx: int, end_idx: int, words: Sequence[Word], max_s: float) -> int | None:
    """Si el clip es muy largo, recorta al último fin de frase (o palabra) dentro de `max_s`."""
    limit = words[start_idx]["s"] + max_s
    candidates = [i for i in range(start_idx, end_idx + 1) if words[i]["e"] <= limit]
    if not candidates:
        return None
    sentence = [i for i in candidates if words[i]["w"].endswith(SENTENCE_END)]
    return sentence[-1] if sentence else candidates[-1]


def postprocess(cands: Sequence[Candidate], words: Sequence[Word], min_s: float, max_s: float,
                limit: int | None = None) -> list[dict]:
    """Ajusta a límites de palabra, filtra por duración y elimina solapes (gana el mayor score).

    Devuelve clips ordenados por score descendente.
    """
    if not words:
        return []
    fixed: list[dict] = []
    for c in cands:
        si, ei = snap_start(c.start, words), snap_end(c.end, words)
        if ei < si:
            continue
        if words[ei]["e"] - words[si]["s"] > max_s:
            ei = _fit_end(si, ei, words, max_s)
            if ei is None:
                continue
        start, end = words[si]["s"], words[ei]["e"]
        if not (min_s <= end - start <= max_s):
            continue
        fixed.append({**c.model_dump(), "start": round(start, 3), "end": round(end, 3),
                      "duration": round(end - start, 3)})

    fixed.sort(key=lambda c: (-c["score"], c["start"]))
    kept: list[dict] = []
    for c in fixed:
        if all(c["end"] <= k["start"] or c["start"] >= k["end"] for k in kept):
            kept.append(c)
        if limit and len(kept) >= limit:
            break
    return kept


def _clip_text(clip: dict, words: Sequence[Word]) -> str:
    return " ".join(w["w"] for w in words if clip["start"] <= w["s"] < clip["end"])


def _rerank(llm: LLM, clips: list[dict], words: Sequence[Word], n: int) -> list[dict]:
    """Pasada final: el LLM elige los N mejores entre todos los candidatos."""
    listing = "\n\n".join(
        f"#{i} [{hms(c['start'])}, {c['duration']:.0f}s, score {c['score']}] {c['title']}\n"
        f"Gancho: {c['hook']}\nTexto: {_clip_text(c, words)[:800]}"
        for i, c in enumerate(clips)
    )
    system = ("Eres editor jefe de videos cortos. De esta lista de candidatos elige los "
              f"{n} con más potencial viral y que funcionen solos. Devuelve sus índices ordenados "
              'del mejor al peor como JSON: {"selected": [int]}')
    for _ in range(MAX_RETRIES + 1):
        try:
            data = json.loads(_strip_fences(llm.complete(system, listing, RANK_SCHEMA)))
            idx = [i for i in dict.fromkeys(data["selected"]) if isinstance(i, int) and 0 <= i < len(clips)]
            if idx:
                chosen = [clips[i] for i in idx[:n]]
                # completa con los de mayor score si el LLM devolvió menos de N
                chosen += [c for c in clips if c not in chosen][: n - len(chosen)]
                return chosen
        except (json.JSONDecodeError, KeyError, TypeError):
            log.warning("Respuesta inválida en la pasada final; reintentando")
    log.warning("Pasada final fallida; uso los de mayor score")
    return clips[:n]


# ---------- Etapa ----------

def done(job: Job) -> bool:
    return job.exists(OUTPUT)


def select_highlights(transcript: dict, cfg: Config, n: int, min_s: float, max_s: float, llm: LLM) -> list[dict]:
    words = flatten_words(transcript)
    segments = [s for s in transcript["segments"] if s.get("words")]
    chunks = chunk_segments(segments, cfg.chunk_minutes * 60, cfg.chunk_overlap_minutes * 60)
    system = (resources.files("clipforge.prompts").joinpath("select_system.txt").read_text(encoding="utf-8")
              .replace("{min_s}", f"{min_s:g}").replace("{max_s}", f"{max_s:g}"))
    per_chunk = max(3, min(n, 8))

    cands: list[Candidate] = []
    for i, block in enumerate(chunks, 1):
        log.info("Analizando bloque %d/%d (%s – %s)…", i, len(chunks), hms(block[0]["start"]), hms(block[-1]["end"]))
        user = (f"Bloque {i} de {len(chunks)} del directo. Propón hasta {per_chunk} fragmentos.\n\n"
                + format_segments(block))
        got = _ask_clips(llm, system, user)
        log.info("  %d candidatos", len(got))
        cands.extend(got)

    clips = postprocess(cands, words, min_s, max_s)
    log.info("%d candidatos válidos tras el ajuste (de %d propuestos)", len(clips), len(cands))
    if len(clips) > n and len(chunks) > 1:
        clips = _rerank(llm, clips, words, n)
    clips = sorted(clips[:n], key=lambda c: c["start"])
    for num, c in enumerate(clips, 1):
        c["n"] = num
    return clips


def run_stage(job: Job, cfg: Config, llm: LLM | None = None) -> None:
    opts = job.options
    n, min_s, max_s = opts.get("clips", cfg.clips), opts.get("min", cfg.min_seconds), opts.get("max", cfg.max_seconds)
    clips = select_highlights(job.load_json("transcript.json"), cfg, n, min_s, max_s, llm or make_llm(cfg))
    if not clips:
        raise ClipForgeError("No se encontró ningún fragmento válido. Prueba con otro rango (--min/--max).")
    job.save_json(OUTPUT, {"clips": clips})
    for c in clips:
        log.info("  #%d %s (%.0fs, score %d) %s", c["n"], hms(c["start"]), c["duration"], c["score"], c["title"])
