"""Detección de diseño por clip: cara, dividido (webcam + pantalla) o solo pantalla.

Lógica pura sobre fotogramas ya muestreados, para poder probarla sin video.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

import numpy as np

ANALYSIS_SIZE = (480, 270)  # tamaño reducido para medir actividad y zonas planas


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int

    def scaled(self, sx: float, sy: float) -> "Box":
        return Box(int(self.x * sx), int(self.y * sy), max(1, int(self.w * sx)), max(1, int(self.h * sy)))

    def as_dict(self) -> dict:
        return asdict(self)


def _even(v: float) -> int:
    v = int(v)
    return v - v % 2


def fit_box(cx: float, cy: float, w: float, aspect: float, W: int, H: int) -> Box:
    """Caja de proporción `aspect` (ancho/alto) centrada en (cx, cy), lo más grande posible hasta `w` y dentro del video."""
    w = min(w, W, H * aspect)
    h = w / aspect
    x = min(max(cx - w / 2, 0), W - w)
    y = min(max(cy - h / 2, 0), H - h)
    return Box(_even(x), _even(y), _even(w), _even(h))


def flat_fraction(gray: np.ndarray, block: int = 8, threshold: float = 2.5) -> float:
    """Fracción de bloques de color totalmente plano: alta en editores/pantallas, baja en imagen de cámara."""
    h, w = gray.shape
    b = gray[: h // block * block, : w // block * block].reshape(h // block, block, w // block, block)
    return float((b.astype(np.float32).std(axis=(1, 3)) < threshold).mean())


@dataclass
class LayoutMetrics:
    face_frac: float     # fracción de muestras con cara
    face_width: float    # ancho mediano de la cara / ancho del video
    flat: float          # fracción de zonas planas (pantalla)


def measure(faces: Sequence[Box | None], grays: Sequence[np.ndarray], W: int) -> LayoutMetrics:
    found = [f for f in faces if f is not None]
    return LayoutMetrics(
        face_frac=round(len(found) / max(1, len(faces)), 3),
        face_width=round(float(np.median([f.w for f in found])) / W, 4) if found else 0.0,
        flat=round(float(np.median([flat_fraction(g) for g in grays])), 3) if grays else 0.0,
    )


def decide(m: LayoutMetrics, small_face: float, min_face_frac: float, flat_screen: float) -> str:
    """Reglas: cara grande → face; webcam pequeña sobre pantalla → split; sin cara y pantalla → screen."""
    has_face = m.face_frac >= min_face_frac
    is_screen = m.flat >= flat_screen
    if has_face and m.face_width >= small_face:
        return "face"
    if has_face and is_screen:
        return "split"
    if not has_face and is_screen:
        return "screen"
    return "face"  # cámara lejana o sin cara: seguimiento normal (o centrado)


def median_face(faces: Sequence[Box | None]) -> Box | None:
    found = [f for f in faces if f is not None]
    if not found:
        return None
    arr = np.array([[f.x, f.y, f.w, f.h] for f in found], dtype=float)
    x, y, w, h = np.median(arr, axis=0)
    return Box(int(x), int(y), int(w), int(h))


def camera_box(face: Box, W: int, H: int, aspect: float, zoom: float) -> Box:
    """Recorte de la webcam: `zoom` veces el ancho de la cara, con la cara algo por encima del centro."""
    cx, cy = face.x + face.w / 2, face.y + face.h / 2 + face.h * 0.35
    return fit_box(cx, cy, face.w * zoom, aspect, W, H)


def activity_map(grays: Sequence[np.ndarray]) -> np.ndarray:
    """Dónde pasa algo en la pantalla: cambios entre muestras (escribir, scroll) + densidad de texto."""
    import cv2

    h, w = grays[0].shape
    act = np.zeros((h, w), np.float32)
    for a, b in zip(grays, grays[1:]):
        act += (cv2.absdiff(a, b) > 12).astype(np.float32)
    edges = np.mean([(cv2.Canny(g, 80, 160) > 0).astype(np.float32) for g in grays], axis=0)
    act = cv2.GaussianBlur(act, (0, 0), 3)
    edges = cv2.GaussianBlur(edges, (0, 0), 3)
    norm = lambda m: m / m.max() if m.max() > 0 else m  # noqa: E731
    return 0.6 * norm(act) + 0.4 * norm(edges)


def best_window(score: np.ndarray, ww: int, wh: int, step: int = 2) -> tuple[int, int]:
    """Esquina (x, y) de la ventana ww×wh con más puntuación (imagen integral)."""
    h, w = score.shape
    ww, wh = min(ww, w), min(wh, h)
    ii = np.pad(score.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    best, pos = -1.0, (0, 0)
    for y in range(0, h - wh + 1, step):
        for x in range(0, w - ww + 1, step):
            s = ii[y + wh, x + ww] - ii[y, x + ww] - ii[y + wh, x] + ii[y, x]
            if s > best:
                best, pos = s, (x, y)
    return pos


def screen_box(grays: Sequence[np.ndarray], W: int, H: int, aspect: float, height_frac: float,
               exclude: Box | None = None) -> Box:
    """Ventana fija de la pantalla con más actividad, evitando la webcam."""
    h_full = min(H, int(H * height_frac))
    w_full = min(W, h_full * aspect)
    if not grays:
        return fit_box(W / 2, H / 2, w_full, aspect, W, H)
    score = activity_map(grays)
    sh, sw = score.shape
    sx, sy = sw / W, sh / H
    if exclude is not None:
        e = exclude.scaled(sx, sy)
        pad = 4
        score[max(0, e.y - pad): e.y + e.h + pad, max(0, e.x - pad): e.x + e.w + pad] = 0
    x, y = best_window(score, int(w_full * sx), int(h_full * sy))
    return fit_box(x / sx + w_full / 2, y / sy + h_full / 2, w_full, aspect, W, H)
