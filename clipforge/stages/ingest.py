"""Etapa 1: descarga (YouTube) o copia (archivo local) → source.mp4."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from clipforge import ClipForgeError
from clipforge.config import Config
from clipforge.job import Job
from clipforge.utils.ffmpeg import probe, run

log = logging.getLogger(__name__)
OUTPUT = "source.mp4"


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://", "www.", "youtu"))


def done(job: Job) -> bool:
    return job.exists(OUTPUT)


def _download(url: str, out: Path) -> None:
    import yt_dlp

    opts_info = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise ClipForgeError(f"No se pudo obtener información del video: {exc}\n"
                             "Prueba a actualizar yt-dlp (`pip install -U yt-dlp`) o usa un archivo local.") from exc
    status = info.get("live_status")
    if info.get("is_live") or status in ("is_live", "is_upcoming"):
        raise ClipForgeError("El directo todavía está en vivo o no ha empezado. "
                             "Espera a que termine y YouTube genere el VOD.")
    log.info("Descargando «%s» (%s)", info.get("title"), info.get("duration_string", "?"))

    opts = {
        "format": "bv*[height<=1080]+ba/b[height<=1080]",
        "merge_output_format": "mp4",
        "outtmpl": str(out.with_suffix(".%(ext)s")),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:  # noqa: BLE001
        raise ClipForgeError(f"Falló la descarga: {exc}") from exc
    if not out.exists():
        raise ClipForgeError("yt-dlp terminó pero no generó source.mp4.")


def _import_local(path: Path, out: Path) -> None:
    if not path.exists():
        raise ClipForgeError(f"No existe el archivo {path}.")
    if path.suffix.lower() == ".mp4":
        try:
            os.link(path, out)  # enlace duro: instantáneo y sin duplicar espacio
        except OSError:
            shutil.copy2(path, out)
    else:
        log.info("Remuxeando %s a MP4", path.name)
        run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
             "-c", "copy", "-movflags", "+faststart", str(out)])


def run_stage(job: Job, cfg: Config) -> None:
    source: str = job.meta["source"]
    out = job.path(OUTPUT)
    tmp = job.path("source.part.mp4")
    tmp.unlink(missing_ok=True)
    if is_url(source):
        _download(source, tmp)
    else:
        _import_local(Path(source).expanduser(), tmp)
    info = probe(tmp)
    if info.duration <= 0:
        tmp.unlink(missing_ok=True)
        raise ClipForgeError("El video tiene duración 0. Si es un directo, espera a que finalice.")
    tmp.replace(out)
    job.meta["video"] = {"duration": info.duration, "width": info.width, "height": info.height, "fps": info.fps}
    job.save_meta()
    log.info("Video listo: %dx%d, %.1f fps, %.0f s", info.width, info.height, info.fps, info.duration)
