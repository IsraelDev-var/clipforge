"""Etapa 7: corte + recorte 9:16 dinámico + escala + subtítulos → clip_<n>.mp4."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from clipforge.config import Config
from clipforge.job import Job
from clipforge.stages.captions import subs_name
from clipforge.stages.reframe import track_name
from clipforge.utils.ffmpeg import run

log = logging.getLogger(__name__)


def clip_name(n: int) -> str:
    return f"clip_{n}.mp4"


def crop_commands(points: list[dict], duration: float, fps: float) -> str:
    """Comandos `sendcmd` para mover el recorte, interpolando linealmente a la tasa del video."""
    if len(points) <= 1:
        return ""
    lines, j = [], 0
    frames = int(duration * fps) + 1
    for f in range(frames):
        t = f / fps
        while j + 1 < len(points) - 1 and points[j + 1]["t"] <= t:
            j += 1
        a, b = points[j], points[j + 1]
        k = 0.0 if b["t"] == a["t"] else min(1.0, max(0.0, (t - a["t"]) / (b["t"] - a["t"])))
        lines.append(f"{t:.3f} crop x {round(a['x'] + k * (b['x'] - a['x']))};")
    return "\n".join(lines) + "\n"


def select_expr(segments: list | None, duration: float) -> str | None:
    """Expresión para los filtros `select`/`aselect` que conserva solo los tramos con voz."""
    if not segments:
        return None
    if len(segments) == 1 and segments[0][0] <= 0.001 and segments[0][1] >= duration - 0.001:
        return None  # no hay nada que recortar
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in segments)


def render_clip(job: Job, cfg: Config, clip: dict) -> Path:
    n = clip["n"]
    duration = clip["end"] - clip["start"]
    track = job.load_json(track_name(n))
    ow, oh = cfg.output_resolution
    fps = job.meta.get("video", {}).get("fps", 30.0)

    vf = []
    cmds = crop_commands(track["points"], duration, fps)
    if cmds:
        job.path(f"crop_cmds_{n}.txt").write_text(cmds, encoding="utf-8")
        vf.append(f"sendcmd=f=crop_cmds_{n}.txt")
    vf.append(f"crop=w={track['width']}:h={track['height']}:x={track['points'][0]['x']}:y={track['y']}")
    # El recorte sigue la cara con los tiempos originales; después se quitan los silencios
    # (antes de escalar, para no procesar fotogramas descartados) y se renumeran los tiempos.
    # Los subtítulos ya vienen ajustados al clip recortado.
    keep = select_expr(clip.get("segments"), duration)
    af = []
    if keep:
        vf.append(f"select='{keep}',setpts=N/FRAME_RATE/TB")
        af.append(f"aselect='{keep}',asetpts=N/SR/TB")
    vf.append(f"scale={ow}:{oh}:flags=lanczos,setsar=1")
    if job.exists(subs_name(n)):
        ass = f"ass={subs_name(n)}"
        fonts = Path(cfg.captions.fonts_dir).resolve() if cfg.captions.fonts_dir else None
        if fonts and fonts.is_dir():
            ass += f":fontsdir='{Path(os.path.relpath(fonts, job.dir.resolve())).as_posix()}'"
        vf.append(ass)

    tmp = f"clip_{n}.part.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{clip['start']:.3f}", "-t", f"{duration:.3f}",
           "-i", "source.mp4", "-vf", ",".join(vf),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart"]
    if cfg.loudnorm:
        af.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    if af:
        cmd += ["-af", ",".join(af)]
    run(cmd + [tmp], cwd=job.dir)  # cwd = carpeta del job: evita escapar rutas en los filtros
    out = job.path(clip_name(n))
    job.path(tmp).replace(out)
    return out


def done(job: Job) -> bool:
    clips = job.load_json("highlights.json")["clips"] if job.exists("highlights.json") else []
    return bool(clips) and all(job.exists(clip_name(c["n"])) for c in clips)


def run_stage(job: Job, cfg: Config, only: int | None = None) -> None:
    clips = job.load_json("highlights.json")["clips"]
    for clip in clips:
        if only is not None and clip["n"] != only:
            continue
        log.info("Renderizando clip %d/%d (%.0fs)…", clip["n"], len(clips), clip["end"] - clip["start"])
        render_clip(job, cfg, clip)
