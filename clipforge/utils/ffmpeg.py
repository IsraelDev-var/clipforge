"""Envoltorios mínimos de ffmpeg/ffprobe."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from clipforge import ClipForgeError

log = logging.getLogger(__name__)


def require_ffmpeg() -> None:
    """Falla con un mensaje claro si ffmpeg/ffprobe no están en el PATH."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise ClipForgeError(f"No se encontró `{tool}` en el PATH. Instala ffmpeg: https://ffmpeg.org/download.html")


def run(cmd: list[str], cwd: Path | None = None) -> None:
    """Ejecuta un comando; si falla muestra las últimas líneas de stderr."""
    log.debug("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise ClipForgeError(f"Falló `{cmd[0]}` (código {proc.returncode}):\n{tail}")


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float


def probe(path: Path) -> VideoInfo:
    """Devuelve duración, tamaño y fps del primer stream de video."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ClipForgeError(f"No se pudo leer el video {path}: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise ClipForgeError(f"El archivo {path} no contiene video.")
    num, _, den = video.get("avg_frame_rate", "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    return VideoInfo(duration=duration, width=int(video["width"]), height=int(video["height"]), fps=fps or 30.0)
