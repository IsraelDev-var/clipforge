from clipforge.stages.render import select_expr
from clipforge.stages.silences import edited_duration, keep_segments, remap, remap_words


def W(w, s, e):
    return {"w": w, "s": s, "e": e}


WORDS = [W("hola", 1.0, 1.4), W("a", 1.5, 1.7), W("todos.", 1.8, 2.2),  # 1 s de silencio inicial
         W("vamos", 4.2, 4.6), W("a", 4.7, 4.8), W("programar", 4.9, 5.6)]  # pausa de 2 s


def test_cuts_leading_long_pauses_and_trailing():
    segs = keep_segments(WORDS, duration=8.0, max_gap=0.5, pad=0.1)
    assert segs == [(0.9, 2.3), (4.1, 5.7)]
    assert edited_duration(segs) == 3.0


def test_short_pauses_are_kept():
    segs = keep_segments(WORDS[:3], duration=2.2, max_gap=0.5, pad=0.1)
    assert segs == [(0.9, 2.2)]  # pausas de 0.1 s no se tocan; el final no pasa de la duración


def test_tiny_isolated_segments_are_dropped():
    words = [W("a", 0.0, 0.5), W("eh", 1.5, 1.55), W("b", 2.6, 3.0)]
    segs = keep_segments(words, duration=3.0, max_gap=0.5, pad=0.0, min_segment=0.2)
    assert segs == [(0.0, 0.5), (2.6, 3.0)]  # sin reintroducir el silencio alrededor de «eh»


def test_remap_times_and_gaps():
    segs = [(0.9, 2.3), (4.1, 5.7)]
    assert remap(0.9, segs) == 0
    assert round(remap(2.0, segs), 3) == 1.1
    assert round(remap(3.0, segs), 3) == 1.4  # dentro de un hueco recortado → inicio del siguiente tramo
    assert round(remap(4.2, segs), 3) == 1.5
    assert round(remap(9.0, segs), 3) == 3.0


def test_remap_words_keeps_order_and_sync():
    segs = keep_segments(WORDS, 8.0, 0.5, 0.1)
    out = remap_words(WORDS, segs)
    assert [w["w"] for w in out] == [w["w"] for w in WORDS]
    assert out[0]["s"] == 0.1 and out[3]["s"] == 1.5
    # las duraciones de cada palabra no cambian → subtítulos sincronizados con el audio recortado
    assert all(abs((o["e"] - o["s"]) - (w["e"] - w["s"])) < 1e-6 for o, w in zip(out, WORDS))
    assert remap_words(WORDS, None) == WORDS


def test_select_expr():
    assert select_expr(None, 10) is None
    assert select_expr([[0.0, 10.0]], 10) is None
    assert select_expr([[0.9, 2.3], [4.1, 5.7]], 8) == "between(t,0.900,2.300)+between(t,4.100,5.700)"
