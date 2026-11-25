from clipforge.config import CaptionsConfig
from clipforge.stages.captions import build_ass, build_events, clip_words, group_words
from clipforge.stages.reframe import crop_box, smooth_track
from clipforge.stages.render import crop_commands
from clipforge.utils.timing import ass_time


def W(w, s, e):
    return {"w": w, "s": s, "e": e}


def test_groups_respect_size_and_chars():
    words = [W("hola", 0, .3), W("a", .3, .4), W("todos", .4, .8), W("bienvenidos", .8, 1.4), W("al", 1.4, 1.5)]
    chunks = group_words(words, 3, 20)
    assert all(len(c) <= 3 for c in chunks)
    assert all(len(" ".join(w["w"] for w in c)) <= 20 for c in chunks)
    assert [w["w"] for c in chunks for w in c] == [w["w"] for w in words]


def test_breaks_on_punctuation_and_silence():
    words = [W("listo.", 0, .3), W("ahora", .4, .7), W("vamos", 2.0, 2.3)]
    chunks = group_words(words, 4, 30)
    assert [[w["w"] for w in c] for c in chunks] == [["listo."], ["ahora"], ["vamos"]]


def test_events_highlight_each_word_in_sync():
    words = [W("uno", 1.0, 1.3), W("dos", 1.35, 1.7), W("tres", 1.8, 2.2)]
    cc = CaptionsConfig(uppercase=False)
    events = build_events(group_words(words, 3, 20), cc)
    assert [e[0] for e in events] == [1.0, 1.35, 1.8]  # cada evento empieza justo con su palabra
    assert events[-1][1] == 2.2
    for (s, e, text), w in zip(events, words):
        assert "{\\c&H00FFFF&}" + w["w"] + "{\\r}" in text


def test_clip_words_relative_times():
    words = [W("a", 9.0, 9.5), W("b", 10.2, 10.6), W("c", 20.0, 20.4)]
    out = clip_words(words, 10.0, 15.0)
    assert out == [{"w": "b", "s": 0.2, "e": 0.6}]


def test_ass_document_and_time_format():
    assert ass_time(3723.456) == "1:02:03.46"
    doc = build_ass([W("hola", 0.0, 0.5)], CaptionsConfig(), (1080, 1920))
    assert "PlayResY: 1920" in doc and "HOLA" in doc and "Dialogue: 0,0:00:00.00,0:00:00.50" in doc


def test_crop_box_even_9_16():
    assert crop_box(1920, 1080) == (606, 1080)
    assert crop_box(1080, 1920) == (1080, 1920)


def test_smoothing_dead_zone_and_gaps():
    jitter = [None, 500, 510, 492, 505, None, 497, 508, 495, 503]
    xs = smooth_track(jitter, alpha=0.3, dead_zone=20, default=960)
    assert len(xs) == len(jitter)
    assert all(x == 500 for x in xs)  # hueco inicial = primera cara; temblores < zona muerta no mueven nada

    jump = [500] * 10 + [900] * 20
    ys = smooth_track(jump, alpha=0.3, dead_zone=20, default=960)
    assert ys[-1] > 890 and ys[0] < 510  # los cambios grandes sí se siguen
    assert all(abs(b - a) < 80 for a, b in zip(ys, ys[1:]))  # sin saltos bruscos


def test_crop_commands_interpolate():
    cmds = crop_commands([{"t": 0, "x": 0}, {"t": 1, "x": 100}], 1.0, 10).splitlines()
    assert cmds[0] == "0.000 crop x 0;"
    assert cmds[5] == "0.500 crop x 50;"
    assert cmds[-1] == "1.000 crop x 100;"
    assert crop_commands([{"t": 0, "x": 5}], 3, 30) == ""
