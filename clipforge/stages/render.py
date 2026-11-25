"""Render: corte + encuadre (cara, dividido o pantalla) + silencios + subtítulos → clip_<n>.mp4."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from clipforge.config import Config
from clipforge.job import Job
from clipforge.stages.captions import subs_name
from clipforge.stages.reframe import panel_sizes, track_name
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


def _crop(b: dict) -> str:
    return f"crop={b['w']}:{b['h']}:{b['x']}:{b['y']}"


def video_graph(track: dict, keep: str | None, out_size: tuple[int, int], cam_h: int,
                sendcmd: str | None, subs: str | None) -> str:
    """Grafo de filtros (filter_complex) para cada diseño; la salida se llama [v]."""
    ow, oh = out_size
    cut = f"select='{keep}',setpts=N/FRAME_RATE/TB," if keep else ""
    tail = f",{subs}" if subs else ""
    mode = track["mode"]
    if mode == "split":
        return (f"[0:v]{cut}split=2[c][s];"
                f"[c]{_crop(track['camera'])},scale={ow}:{cam_h}:flags=lanczos,setsar=1[top];"
                f"[s]{_crop(track['screen'])},scale={ow}:{oh - cam_h}:flags=lanczos,setsar=1[bot];"
                f"[top][bot]vstack{tail}[v]")
    if mode == "screen":
        return (f"[0:v]{cut}split=2[b][f];"
                f"[b]scale={ow}:{oh}:force_original_aspect_ratio=increase,crop={ow}:{oh},"
                f"boxblur=luma_radius=40:luma_power=2,eq=brightness=-0.12,setsar=1[bg];"
                f"[f]{_crop(track['screen'])},scale={ow}:{oh - cam_h}:flags=lanczos,setsar=1[fg];"
                f"[bg][fg]overlay=0:{cam_h // 2}{tail}[v]")
    # face / center: el recorte sigue la cara con los tiempos originales; después se quitan los
    # silencios (antes de escalar, para no procesar fotogramas descartados).
    first = track["points"][0]["x"]
    pre = f"sendcmd=f={sendcmd}," if sendcmd else ""
    return (f"[0:v]{pre}crop=w={track['width']}:h={track['height']}:x={first}:y={track['y']},"
            f"{cut}scale={ow}:{oh}:flags=lanczos,setsar=1{tail}[v]")


def render_clip(job: Job, cfg: Config, clip: dict) -> Path:
    n = clip["n"]
    duration = clip["end"] - clip["start"]
    track = job.load_json(track_name(n))
    fps = job.meta.get("video", {}).get("fps", 30.0)
    (_, cam_h), _ = panel_sizes(cfg)

    sendcmd = None
    if track["mode"] in ("face", "center"):
        cmds = crop_commands(track["points"], duration, fps)
        if cmds:
            sendcmd = f"crop_cmds_{n}.txt"
            job.path(sendcmd).write_text(cmds, encoding="utf-8")

    subs = None
    if job.exists(subs_name(n)):
        subs = f"ass={subs_name(n)}"
        fonts = Path(cfg.captions.fonts_dir).resolve() if cfg.captions.fonts_dir else None
        if fonts and fonts.is_dir():
            subs += f":fontsdir='{Path(os.path.relpath(fonts, job.dir.resolve())).as_posix()}'"

    # Los subtítulos ya vienen ajustados al clip sin silencios.
    keep = select_expr(clip.get("segments"), duration)
    graph = video_graph(track, keep, cfg.output_resolution, cam_h, sendcmd, subs)
    af = [f"aselect='{keep}',asetpts=N/SR/TB"] if keep else []
    if cfg.loudnorm:
        af.append("loudnorm=I=-14:TP=-1.5:LRA=11")

    tmp = f"clip_{n}.part.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{clip['start']:.3f}", "-t", f"{duration:.3f}",
           "-i", "source.mp4", "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart"]
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
