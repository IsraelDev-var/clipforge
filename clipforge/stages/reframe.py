"""Etapa 5: sigue la cara para el recorte 9:16 → crop_track_<n>.json."""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path
from typing import Sequence

from clipforge.config import Config
from clipforge.job import Job

log = logging.getLogger(__name__)
TARGET_ASPECT = 9 / 16
DETECT_WIDTH = 640  # se reduce el fotograma a este ancho para detectar más rápido
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
             "blaze_face_short_range/float16/1/blaze_face_short_range.tflite")
MODEL_PATH = Path.home() / ".cache" / "clipforge" / "blaze_face_short_range.tflite"


def track_name(n: int) -> str:
    return f"crop_track_{n}.json"


def crop_box(width: int, height: int) -> tuple[int, int]:
    """Tamaño (par) de la ventana 9:16 más grande que cabe en el video."""
    if width / height > TARGET_ASPECT:
        cw, ch = int(height * TARGET_ASPECT), height
    else:
        cw, ch = width, int(width / TARGET_ASPECT)
    return cw - cw % 2, ch - ch % 2


# ---------- Detectores ----------

class _MediaPipeDetector:
    def __init__(self) -> None:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        if not MODEL_PATH.exists():
            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            log.info("Descargando modelo de detección de caras…")
            urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        self._mp = mp
        self._det = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL_PATH)), min_detection_confidence=0.5))

    def __call__(self, rgb) -> list[tuple[float, float]]:
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        out = []
        for d in self._det.detect(img).detections:
            b = d.bounding_box
            score = d.categories[0].score if d.categories else 1.0
            out.append((b.origin_x + b.width / 2, b.width * b.height * score))
        return out

    def close(self) -> None:
        self._det.close()


class _HaarDetector:
    def __init__(self) -> None:
        import cv2

        self._cv2 = cv2
        self._det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    def __call__(self, rgb) -> list[tuple[float, float]]:
        gray = self._cv2.cvtColor(rgb, self._cv2.COLOR_RGB2GRAY)
        faces = self._det.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(40, 40))
        return [(x + w / 2, float(w * h)) for (x, y, w, h) in faces]


    def close(self) -> None:
        self._det.close()


class _HybridDetector:
    """MediaPipe primero; si no ve cara (p. ej. webcam pequeña en la esquina), prueba Haar."""

    def __init__(self) -> None:
        self.primary: _MediaPipeDetector | None = None
        try:
            self.primary = _MediaPipeDetector()
        except Exception as exc:  # noqa: BLE001
            log.warning("MediaPipe no disponible (%s); uso solo el detector Haar de OpenCV", exc)
        self.fallback = _HaarDetector()

    def __call__(self, rgb) -> list[tuple[float, float]]:
        faces = self.primary(rgb) if self.primary else []
        return faces or self.fallback(rgb)

    def close(self) -> None:
        if self.primary:
            self.primary.close()
            self.primary = None


_detector: _HybridDetector | None = None


def get_detector() -> _HybridDetector:
    global _detector
    if _detector is None:
        _detector = _HybridDetector()
    return _detector


def close_detector() -> None:
    global _detector
    if _detector is not None:
        _detector.close()
        _detector = None


# ---------- Suavizado (lógica pura) ----------

def fill_gaps(raw: Sequence[float | None], default: float) -> list[float]:
    """Sin cara: mantiene la última posición conocida (o la primera futura al inicio, o el centro)."""
    first = next((v for v in raw if v is not None), default)
    out, last = [], first
    for v in raw:
        last = v if v is not None else last
        out.append(last)
    return out


def smooth_track(raw: Sequence[float | None], alpha: float, dead_zone: float, default: float) -> list[float]:
    """Zona muerta + EMA hacia delante y hacia atrás (sin retardo de fase)."""
    xs = fill_gaps(raw, default)
    if not xs:
        return []
    # Zona muerta: el objetivo solo cambia si la cara se aleja más de `dead_zone` píxeles.
    target, held = xs[0], []
    for x in xs:
        if abs(x - target) > dead_zone:
            target = x
        held.append(target)

    def ema(seq: list[float]) -> list[float]:
        out, acc = [], seq[0]
        for v in seq:
            acc += alpha * (v - acc)
            out.append(acc)
        return out

    return ema(ema(held)[::-1])[::-1]


# ---------- Etapa ----------

def detect_centers(source: Path, start: float, end: float, sample_fps: float) -> tuple[list[float | None], float]:
    """Centro X de la cara principal en cada muestra (o None). Devuelve también el paso entre muestras."""
    import cv2

    cap = cv2.VideoCapture(str(source))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    every = max(1, round(fps / sample_fps))
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    detector = get_detector()
    scale = min(1.0, DETECT_WIDTH / width)
    centers: list[float | None] = []
    frame_i, total = 0, int((end - start) * fps)
    while frame_i < total:
        if frame_i % every == 0:
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
            faces = detector(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
            centers.append(max(faces, key=lambda f: f[1])[0] / scale if faces else None)
        elif not cap.grab():
            break
        frame_i += 1
    cap.release()
    return centers, every / fps


def done(job: Job) -> bool:
    clips = job.load_json("highlights.json")["clips"] if job.exists("highlights.json") else []
    return bool(clips) and all(job.exists(track_name(c["n"])) for c in clips)


def reframe_clip(job: Job, cfg: Config, clip: dict, use_faces: bool) -> None:
    v = job.meta["video"]
    W, H = v["width"], v["height"]
    cw, ch = crop_box(W, H)
    center_x, y = (W - cw) // 2, (H - ch) // 2
    points: list[dict] = [{"t": 0.0, "x": center_x}]
    mode = "center"
    if use_faces and cw < W:
        raw, step = detect_centers(job.path("source.mp4"), clip["start"], clip["end"], cfg.reframe.sample_fps)
        found = sum(r is not None for r in raw)
        if found:
            mode = "face"
            xs = smooth_track(raw, cfg.reframe.smoothing, cfg.reframe.dead_zone * cw, W / 2)
            points = [{"t": round(i * step, 3), "x": int(min(max(x - cw / 2, 0), W - cw))} for i, x in enumerate(xs)]
        log.info("  clip %d: cara detectada en %d/%d muestras", clip["n"], found, len(raw))
    job.save_json(track_name(clip["n"]), {"mode": mode, "width": cw, "height": ch, "y": y,
                                          "src_width": W, "src_height": H, "points": points})


def run_stage(job: Job, cfg: Config, only: int | None = None) -> None:
    use_faces = job.options.get("faces", True)
    try:
        for clip in job.load_json("highlights.json")["clips"]:
            if only is None or clip["n"] == only:
                reframe_clip(job, cfg, clip, use_faces)
    finally:
        close_detector()
