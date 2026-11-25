"""Representa un trabajo: carpeta `data/<job_id>/` con sus archivos intermedios."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clipforge import ClipForgeError

DATA_DIR = Path("data")


@dataclass
class Job:
    id: str
    dir: Path
    meta: dict[str, Any] = field(default_factory=dict)

    def path(self, name: str) -> Path:
        return self.dir / name

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def load_json(self, name: str) -> Any:
        p = self.path(name)
        if not p.exists():
            raise ClipForgeError(f"Falta {p}. Ejecuta antes la etapa que lo genera.")
        return json.loads(p.read_text(encoding="utf-8"))

    def save_json(self, name: str, data: Any) -> Path:
        p = self.path(name)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)  # escritura atómica: una etapa a medias no deja JSON corrupto
        return p

    def save_meta(self) -> None:
        self.save_json("job.json", self.meta)

    def set_status(self, state: str, stage: str | None = None, message: str = "") -> None:
        """Estado visible desde la web: queued | running | done | error."""
        self.meta["status"] = {"state": state, "stage": stage, "message": message, "updated": time.time()}
        self.save_meta()

    @property
    def status(self) -> dict[str, Any]:
        return self.meta.get("status") or {"state": "new", "stage": None, "message": ""}

    @property
    def options(self) -> dict[str, Any]:
        return self.meta.setdefault("options", {})


def _slug(source: str) -> str:
    if "v=" in source:  # URL de YouTube → id del video
        base = source.split("v=")[1].split("&")[0]
    elif source.startswith(("http://", "https://")):
        base = source.rstrip("/").rsplit("/", 1)[-1]
    else:
        base = Path(source).stem
    return re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-")[:30].lower() or "job"


def create_job(source: str, options: dict[str, Any], data_dir: Path = DATA_DIR, job_id: str | None = None) -> Job:
    """Crea (o reutiliza si ya existe) la carpeta de un trabajo."""
    job_id = job_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{_slug(source)}"
    d = data_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    job = Job(job_id, d)
    if job.exists("job.json"):
        job.meta = job.load_json("job.json")
    job.meta.update({"id": job_id, "source": source})
    job.options.update(options)
    job.save_meta()
    return job


def open_job(job_id: str, data_dir: Path = DATA_DIR) -> Job:
    """Abre un trabajo existente."""
    d = data_dir / job_id
    if not (d / "job.json").exists():
        raise ClipForgeError(f"No existe el trabajo `{job_id}` en {data_dir}/.")
    job = Job(job_id, d)
    job.meta = job.load_json("job.json")
    return job
