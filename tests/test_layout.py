import numpy as np

from clipforge.config import Config
from clipforge.stages.captions import margin_for_layout
from clipforge.stages.layout import (Box, LayoutMetrics, best_window, camera_box, decide, fit_box, flat_fraction,
                                     measure, screen_box)
from clipforge.stages.render import video_graph

ARGS = dict(small_face=0.06, min_face_frac=0.3, flat_screen=0.45)


def test_decide_rules():
    assert decide(LayoutMetrics(1.0, 0.15, 0.2), **ARGS) == "face"     # cámara normal
    assert decide(LayoutMetrics(1.0, 0.15, 0.8), **ARGS) == "face"     # cara grande aunque haya fondo plano
    assert decide(LayoutMetrics(0.9, 0.035, 0.8), **ARGS) == "split"   # webcam pequeña sobre el editor
    assert decide(LayoutMetrics(0.0, 0.0, 0.85), **ARGS) == "screen"   # solo pantalla
    assert decide(LayoutMetrics(0.9, 0.03, 0.15), **ARGS) == "face"    # persona lejos de la cámara
    assert decide(LayoutMetrics(0.1, 0.03, 0.8), **ARGS) == "screen"   # cara esporádica (falso positivo)


def test_flat_fraction_screen_vs_camera():
    rng = np.random.default_rng(0)
    screen = np.full((270, 480), 35, np.uint8)
    screen[50:60, 100:300] = 200  # una línea de "texto"
    camera = rng.normal(120, 20, (270, 480)).clip(0, 255).astype(np.uint8)
    assert flat_fraction(screen) > 0.9
    assert flat_fraction(camera) < 0.05


def test_measure_uses_median_face():
    faces = [Box(1600, 700, 70, 70), None, Box(1602, 702, 66, 66), Box(1598, 698, 72, 72)]
    m = measure(faces, [], 1920)
    assert m.face_frac == 0.75 and abs(m.face_width - 70 / 1920) < 1e-3


def test_boxes_keep_aspect_even_and_inside():
    b = fit_box(1900, 50, 800, 1080 / 768, 1920, 1080)
    assert b.x + b.w <= 1920 and b.y >= 0 and b.w % 2 == 0 and b.h % 2 == 0
    assert abs(b.w / b.h - 1080 / 768) < 0.02
    cam = camera_box(Box(1660, 740, 70, 70), 1920, 1080, 1080 / 768, 3.2)
    assert cam.x <= 1660 and cam.x + cam.w >= 1730  # la cara entera dentro del recorte


def test_screen_box_follows_activity_and_avoids_camera():
    grays = []
    for i in range(6):  # alguien escribe en la mitad derecha
        g = np.full((270, 480), 30, np.uint8)
        g[40:40 + 10 * i, 300:460] = 220
        grays.append(g)
    box = screen_box(grays, 1920, 1080, 1080 / 1152, 1.0)
    assert box.x > 600  # se va a la derecha, donde hay actividad
    # si la webcam tapa esa zona, la ventana la evita
    cam = Box(1100, 0, 820, 1080)
    box2 = screen_box(grays, 1920, 1080, 1080 / 1152, 1.0, exclude=cam)
    assert box2.x + box2.w <= 1100 + 8


def test_best_window_integral():
    s = np.zeros((20, 40))
    s[5:10, 30:38] = 1
    assert best_window(s, 10, 10, step=1)[0] in range(28, 31)


def test_video_graphs_and_caption_positions():
    cfg = Config()
    split = {"mode": "split", "camera": {"x": 1600, "y": 700, "w": 220, "h": 156},
             "screen": {"x": 0, "y": 0, "w": 1012, "h": 1080}}
    g = video_graph(split, "between(t,0,5)", (1080, 1920), 768, None, "ass=subs_1.ass")
    assert "vstack" in g and "scale=1080:768" in g and "scale=1080:1152" in g and g.endswith("[v]")
    assert g.index("select=") < g.index("split=2")  # se quitan silencios antes de dividir
    scr = video_graph({"mode": "screen", "screen": split["screen"]}, None, (1080, 1920), 768, None, None)
    assert "boxblur" in scr and "overlay=0:384" in scr
    face = video_graph({"mode": "face", "width": 606, "height": 1080, "y": 0, "points": [{"t": 0, "x": 10}]},
                       None, (1080, 1920), 768, "crop_cmds_1.txt", None)
    assert face.startswith("[0:v]sendcmd=f=crop_cmds_1.txt,crop=")
    # subtítulos: sobre la línea divisoria (40 %) y debajo del código en «solo pantalla»
    assert abs((1920 - margin_for_layout("split", cfg) - cfg.captions.size / 2) - 768) < 2
    assert margin_for_layout("screen", cfg) < 300
    assert margin_for_layout("face", cfg) == cfg.captions.margin_v
