"""Interfaz web: pegar enlace o subir video → ver clips → ajustar → descargar."""

from __future__ import annotations

import logging
import re
import shutil
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from clipforge import ClipForgeError
from clipforge.config import Config, load_config
from clipforge.job import DATA_DIR, Job, create_job, open_job
from clipforge.pipeline import STAGES, rerender_clip, run_pipeline
from clipforge.stages import metadata
from clipforge.stages.render import clip_name
from clipforge.stages.select import Candidate
from clipforge.utils.timing import flatten_words

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm"}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
CHUNK = 8 * 1024 * 1024


class Worker:
    """Una sola tarea pesada a la vez (Whisper/ffmpeg ya usan todos los núcleos); el resto espera en cola."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge")
        self._lock = threading.Lock()
        self.busy: set[str] = set()

    def submit(self, job: Job, fn: Callable[[], None]) -> None:
        with self._lock:
            if job.id in self.busy:
                raise HTTPException(409, "Este trabajo ya se está procesando.")
            self.busy.add(job.id)
        job.set_status("queued")

        def task() -> None:
            try:
                fn()
            except ClipForgeError as exc:
                log.error("Trabajo %s: %s", job.id, exc)
            except Exception:  # noqa: BLE001
                log.exception("Trabajo %s falló", job.id)
            finally:
                with self._lock:
                    self.busy.discard(job.id)

        self._pool.submit(task)


class ClipText(BaseModel):
    title: str | None = None
    description: str | None = None
    hashtags: list[str] | None = None


class ClipTimes(BaseModel):
    start: float
    end: float


class ResumeReq(BaseModel):
    from_stage: str | None = None


def create_app(data_dir: Path = DATA_DIR, config_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="ClipForge")
    worker = Worker()
    data_dir.mkdir(parents=True, exist_ok=True)

    def cfg() -> Config:
        return load_config(config_path)  # se relee en cada trabajo: cambios en config.yaml sin reiniciar

    def get_job(job_id: str) -> Job:
        if not JOB_ID_RE.match(job_id):
            raise HTTPException(400, "Id de trabajo inválido.")
        try:
            return open_job(job_id, data_dir)
        except ClipForgeError as exc:
            raise HTTPException(404, str(exc)) from exc

    def ensure_idle(job: Job) -> None:
        if job.id in worker.busy:
            raise HTTPException(409, "Espera a que termine el proceso actual de este trabajo.")

    # Trabajos que quedaron a medias si el servidor se cerró en mitad de un proceso.
    for d in data_dir.iterdir():
        if (d / "job.json").exists():
            job = open_job(d.name, data_dir)
            if job.status["state"] in ("queued", "running"):
                job.set_status("error", job.status.get("stage"), "Interrumpido al cerrar el servidor. Pulsa «Reanudar».")

    def job_summary(job: Job) -> dict:
        clips = job.load_json("clips.json")["clips"] if job.exists("clips.json") else []
        return {"id": job.id, "source": job.meta.get("source"), "title": job.meta.get("title"), "status": job.status,
                "busy": job.id in worker.busy, "clips": len(clips),
                "updated": job.path("job.json").stat().st_mtime}

    @app.get("/api/config")
    def get_config() -> dict:
        c = cfg()
        return {"clips": c.clips, "min": c.min_seconds, "max": c.max_seconds, "stages": list(STAGES)}

    @app.get("/api/jobs")
    def list_jobs() -> list[dict]:
        jobs = [job_summary(open_job(d.name, data_dir)) for d in data_dir.iterdir() if (d / "job.json").exists()]
        return sorted(jobs, key=lambda j: -j["updated"])

    @app.post("/api/jobs", status_code=201)
    def new_job(
        url: str | None = Form(None),
        file: UploadFile | None = File(None),
        clips: int = Form(...),
        min_s: float = Form(...),
        max_s: float = Form(...),
        faces: bool = Form(True),
    ) -> dict:
        if not (1 <= clips <= 30):
            raise HTTPException(422, "El número de clips debe estar entre 1 y 30.")
        if not (0 < min_s < max_s <= 180):
            raise HTTPException(422, "Duraciones inválidas: 0 < mínimo < máximo ≤ 180 s.")
        opts = {"clips": clips, "min": min_s, "max": max_s, "faces": faces}
        url = (url or "").strip()
        if file is not None and file.filename:
            ext = Path(file.filename).suffix.lower()
            if ext not in VIDEO_EXT:
                raise HTTPException(422, f"Formato no soportado ({ext}). Usa {', '.join(sorted(VIDEO_EXT))}.")
            job = create_job(file.filename, opts, data_dir)
            dest = job.path(f"upload{ext}")
            with dest.open("wb") as out:
                shutil.copyfileobj(file.file, out, CHUNK)
            job.meta["source"] = str(dest.resolve())
            job.meta["title"] = file.filename
            job.save_meta()
        elif url.startswith(("http://", "https://")):
            job = create_job(url, opts, data_dir)
        else:
            raise HTTPException(422, "Pega un enlace de YouTube (https://…) o sube un archivo de video.")
        c = cfg()
        worker.submit(job, lambda: run_pipeline(job, c))
        return {"id": job.id}

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str) -> dict:
        job = get_job(job_id)
        out = job_summary(job) | {"options": job.options, "video": job.meta.get("video"),
                                  "title": job.meta.get("title"), "clips": []}
        if job.exists("highlights.json"):
            words = flatten_words(job.load_json("transcript.json")) if job.exists("transcript.json") else []
            for c in job.load_json("highlights.json")["clips"]:
                f = job.path(clip_name(c["n"]))
                text = " ".join(w["w"] for w in words if c["start"] <= w["s"] < c["end"])
                out["clips"].append(c | {"file": f.name if f.exists() else None,
                                         "version": int(f.stat().st_mtime) if f.exists() else 0,
                                         "text": text})
        out["has_source"] = job.exists("source.mp4")
        return out

    @app.patch("/api/jobs/{job_id}/clips/{n}")
    def edit_clip(job_id: str, n: int, body: ClipText) -> dict:
        job = get_job(job_id)
        ensure_idle(job)
        data = job.load_json("highlights.json")
        clip = next((c for c in data["clips"] if c["n"] == n), None)
        if clip is None:
            raise HTTPException(404, f"El clip {n} no existe.")
        changes = body.model_dump(exclude_none=True)
        # Reutiliza la validación de select (título ≤ 60, hashtags con #).
        clean = Candidate.model_validate({**clip, **changes})
        clip.update({k: getattr(clean, k) for k in changes})
        job.save_json("highlights.json", data)
        metadata.run_stage(job, cfg())
        return clip

    @app.post("/api/jobs/{job_id}/clips/{n}/render", status_code=202)
    def rerender(job_id: str, n: int, body: ClipTimes) -> dict:
        job = get_job(job_id)
        ensure_idle(job)
        if body.end - body.start < 1:
            raise HTTPException(422, "El clip debe durar al menos 1 segundo.")
        c = cfg()
        worker.submit(job, lambda: rerender_clip(job, c, n, body.start, body.end))
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/resume", status_code=202)
    def resume(job_id: str, body: ResumeReq) -> dict:
        job = get_job(job_id)
        ensure_idle(job)
        if body.from_stage and body.from_stage not in STAGES:
            raise HTTPException(422, f"Etapa desconocida: {body.from_stage}")
        c = cfg()
        worker.submit(job, lambda: run_pipeline(job, c, body.from_stage))
        return {"ok": True}

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict:
        job = get_job(job_id)
        ensure_idle(job)
        shutil.rmtree(job.dir)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/files/{name}")
    def get_file(job_id: str, name: str, download: bool = False) -> FileResponse:
        job = get_job(job_id)
        if not re.fullmatch(r"source\.mp4|clip_\d+\.mp4|clips\.json", name) or not job.exists(name):
            raise HTTPException(404, "Archivo no encontrado.")
        filename = f"{job.id}_{name}" if download else None
        return FileResponse(job.path(name), filename=filename)

    @app.get("/api/jobs/{job_id}/zip")
    def get_zip(job_id: str) -> FileResponse:
        job = get_job(job_id)
        files = sorted(job.dir.glob("clip_*.mp4"), key=lambda p: p.name)
        files = [f for f in files if ".part" not in f.name]
        if not files:
            raise HTTPException(404, "Todavía no hay clips.")
        zpath = job.path("clips.zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:  # MP4 ya va comprimido
            for f in files + ([job.path("clips.json")] if job.exists("clips.json") else []):
                z.write(f, f.name)
        return FileResponse(zpath, filename=f"{job.id}_clips.zip")

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
