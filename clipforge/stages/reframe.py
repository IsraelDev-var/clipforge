"""Etapa de encuadre: decide el diseño del clip (cara / dividido / pantalla) y genera crop_track_<n>.json."""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path
from typing import Sequence

from clipforge.config import Config
from clipforge.job import Job
from clipforge.stages.layout import ANALYSIS_SIZE, Box, camera_box, decide, measure, median_face, screen_box

log = logging.getLogger(__name__)
TARGET_ASPECT = 9 / 16
DETECT_WIDTH = 640    # ancho para el seguimiento rápido de la cara
SMALL_FACE_WIDTH = 1280  # segunda pasada para webcams pequeñas superpuestas
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
             "blaze_face_short_range/float16/1/blaze_face_short_range.tflite")
MODEL_PATH = Path.home() / ".cache" / "clipforge" / "blaze_face_short_range.tflite"
LAYOUTS = ("auto", "face", "split", "screen")
Detection = tuple[float, float, float, float, float]  # x, y, w, h, score


def track_name(n: int) -> str:
    return f"crop_track_{n}.json"


def crop_box(width: int, height: int) -> tuple[int, int]:
    """Tamaño (par) de la ventana 9:16 más grande que cabe en el video."""
    if width / height > TARGET_ASPECT:
        cw, ch = int(height * TARGET_ASPECT), height
    else:
        cw, ch = width, int(width / TARGET_ASPECT)
    return cw - cw % 2, ch - ch % 2


def panel_sizes(cfg: Config) -> tuple[tuple[int, int], tuple[int, int]]:
    """Tamaños de salida (ancho, alto) de cámara y pantalla en el diseño dividido."""
    ow, oh = cfg.output_resolution
    cam_h = int(oh * cfg.layout.camera_ratio) // 2 * 2
    return (ow, cam_h), (ow, oh - cam_h)


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

    def __call__(self, rgb) -> list[Detection]:
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        out = []
        for d in self._det.detect(img).detections:
            b = d.bounding_box
            score = d.categories[0].score if d.categories else 1.0
            out.append((b.origin_x, b.origin_y, b.width, b.height, score))
        return out

    def close(self) -> None:
        self._det.close()


class _HaarDetector:
    def __init__(self) -> None:
        import cv2

        self._cv2 = cv2
        self._det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    def __call__(self, rgb) -> list[Detection]:
        gray = self._cv2.cvtColor(rgb, self._cv2.COLOR_RGB2GRAY)
        faces = self._det.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(24, 24))
        return [(float(x), float(y), float(w), float(h), 1.0) for (x, y, w, h) in faces]


class _HybridDetector:
    """MediaPipe primero; si no ve cara (p. ej. webcam pequeña en la esquina), prueba Haar."""

    def __init__(self) -> None:
        self.primary: _MediaPipeDetector | None = None
        try:
            self.primary = _MediaPipeDetector()
        except Exception as exc:  # noqa: BLE001
            log.warning("MediaPipe no disponible (%s); uso solo el detector Haar de OpenCV", exc)
        self.haar = _HaarDetector()

    def __call__(self, rgb) -> list[Detection]:
        faces = self.primary(rgb) if self.primary else []
        return faces or self.haar(rgb)

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


def _main_face(dets: list[Detection]) -> Detection | None:
    return max(dets, key=lambda d: d[2] * d[3] * d[4]) if dets else None


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


# ---------- Lectura de video ----------

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
            face = _main_face(detector(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)))
            centers.append((face[0] + face[2] / 2) / scale if face else None)
        elif not cap.grab():
            break
        frame_i += 1
    cap.release()
    return centers, every / fps


def analyze(source: Path, start: float, end: float, samples: int):
    """Muestras repartidas por el clip: caja de la cara (o None) y fotograma reducido en gris."""
    import cv2

    cap = cv2.VideoCapture(str(source))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    detector = get_detector()
    faces: list[Box | None] = []
    grays = []
    n = max(2, samples)
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_MSEC, (start + (end - start) * (i + 0.5) / n) * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        grays.append(cv2.cvtColor(cv2.resize(frame, ANALYSIS_SIZE, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY))
        face = None
        for target in (DETECT_WIDTH, SMALL_FACE_WIDTH):  # segunda pasada: webcams pequeñas
            s = min(1.0, target / W)
            img = cv2.resize(frame, None, fx=s, fy=s) if s < 1 else frame
            d = _main_face(detector(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
            if d:
                face = Box(int(d[0] / s), int(d[1] / s), int(d[2] / s), int(d[3] / s))
                break
        faces.append(face)
    cap.release()
    return faces, grays


# ---------- Etapa ----------

def layout_for(clip: dict, job: Job, cfg: Config) -> str:
    """Ajuste por clip (web) > opción del trabajo > config.yaml."""
    mode = clip.get("layout") or job.options.get("layout") or cfg.layout.mode
    return mode if mode in LAYOUTS else "auto"


def done(job: Job) -> bool:
    clips = job.load_json("highlights.json")["clips"] if job.exists("highlights.json") else []
    return bool(clips) and all(job.exists(track_name(c["n"])) for c in clips)


def _face_track(job: Job, cfg: Config, clip: dict, use_faces: bool) -> dict:
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
    return {"mode": mode, "width": cw, "height": ch, "y": y, "points": points}


def reframe_clip(job: Job, cfg: Config, clip: dict, use_faces: bool) -> None:
    v = job.meta["video"]
    W, H = v["width"], v["height"]
    lc = cfg.layout
    wanted = layout_for(clip, job, cfg)
    (cam_w, cam_h), (scr_w, scr_h) = panel_sizes(cfg)
    track: dict
    metrics = None

    if (W > H and wanted == "auto" and use_faces) or wanted in ("split", "screen"):
        faces, grays = analyze(job.path("source.mp4"), clip["start"], clip["end"], lc.analysis_samples)
        m = measure(faces, grays, W)
        metrics = m.__dict__
        mode = decide(m, lc.small_face, lc.min_face_frac, lc.flat_screen) if wanted == "auto" else wanted
        face = median_face(faces)
        if mode == "split" and face is None:
            log.warning("  clip %d: no se encontró la webcam; uso «solo pantalla»", clip["n"])
            mode = "screen"
        if mode == "split":
            cam = camera_box(face, W, H, cam_w / cam_h, lc.camera_zoom)
            scr = screen_box(grays, W, H, scr_w / scr_h, lc.screen_height, exclude=cam)
            track = {"mode": "split", "camera": cam.as_dict(), "screen": scr.as_dict()}
        elif mode == "screen":
            exclude = camera_box(face, W, H, cam_w / cam_h, lc.camera_zoom) if face else None
            scr = screen_box(grays, W, H, scr_w / scr_h, lc.screen_height, exclude=exclude)
            track = {"mode": "screen", "screen": scr.as_dict()}
        else:
            track = _face_track(job, cfg, clip, use_faces)
        log.info("  clip %d: diseño «%s» (cara en %.0f%% de muestras, ancho %.1f%%, zonas planas %.0f%%)",
                 clip["n"], track["mode"], 100 * m.face_frac, 100 * m.face_width, 100 * m.flat)
    else:
        track = _face_track(job, cfg, clip, use_faces)

    track.update({"src_width": W, "src_height": H, "requested": wanted, "metrics": metrics})
    job.save_json(track_name(clip["n"]), track)


def run_stage(job: Job, cfg: Config, only: int | None = None) -> None:
    use_faces = job.options.get("faces", True)
    try:
        for clip in job.load_json("highlights.json")["clips"]:
            if only is None or clip["n"] == only:
                reframe_clip(job, cfg, clip, use_faces)
    finally:
        close_detector()
