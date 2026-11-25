import json

from clipforge.config import Config
from clipforge.stages.select import Candidate, chunk_segments, postprocess, select_highlights


def make_words(n: int, dur: float = 0.5, gap: float = 0.1, punct_every: int = 10) -> list[dict]:
    words, t = [], 0.0
    for i in range(n):
        w = f"p{i}" + ("." if (i + 1) % punct_every == 0 else "")
        words.append({"w": w, "s": round(t, 3), "e": round(t + dur, 3)})
        t += dur + gap
    return words


WORDS = make_words(300)  # ~180 s


def cand(start, end, score=50, **kw):
    return Candidate(start=start, end=end, score=score, **kw)


def test_snaps_to_word_boundaries():
    out = postprocess([cand(10.05, 40.33)], WORDS, 25, 60)
    starts = {w["s"] for w in WORDS}
    ends = {w["e"] for w in WORDS}
    assert out[0]["start"] in starts and out[0]["end"] in ends
    assert abs(out[0]["start"] - 10.05) < 0.6 and abs(out[0]["end"] - 40.33) < 0.6


def test_discards_out_of_range():
    out = postprocess([cand(0, 10), cand(20, 140)], WORDS, 25, 60)
    # el corto se descarta; el largo se recorta a <= 60 s terminando en fin de frase
    assert len(out) == 1
    assert out[0]["duration"] <= 60
    end_word = next(w for w in WORDS if w["e"] == out[0]["end"])
    assert end_word["w"].endswith(".")


def test_removes_overlaps_keeping_best_score():
    out = postprocess([cand(10, 50, 60), cand(30, 70, 90), cand(100, 140, 40)], WORDS, 25, 60)
    spans = sorted((c["start"], c["end"]) for c in out)
    assert [c["score"] for c in out] == [90, 40]
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 <= s2


def test_limit_and_title_and_hashtags():
    c = cand(10, 40, title="x" * 80, hashtags=["python", "#Dev Tips", ""])
    out = postprocess([c, cand(60, 95), cand(110, 140)], WORDS, 25, 60, limit=2)
    assert len(out) == 2
    assert len(out[0]["title"]) <= 60
    assert out[0]["hashtags"] == ["#python", "#DevTips"]


def test_score_clamped():
    assert cand(0, 30, score=150).score == 100
    assert cand(0, 30, score=-3).score == 1


def test_chunking_with_overlap():
    segs = [{"start": float(t), "end": float(t + 10), "text": "x"} for t in range(0, 1500, 10)]
    chunks = chunk_segments(segs, 600, 60)
    assert len(chunks) == 3
    assert chunks[1][0]["start"] == 540  # empieza 1 min antes del final del bloque anterior
    assert chunks[-1][-1]["end"] == 1500


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, user, schema):
        self.calls += 1
        return self.replies.pop(0)


def _transcript():
    return {"segments": [{"id": 0, "start": 0.0, "end": WORDS[-1]["e"], "text": "x", "words": WORDS}]}


def test_select_retries_invalid_json_and_numbers_clips():
    good = json.dumps({"clips": [
        {"start": 100, "end": 140, "score": 70, "hook": "h", "title": "B", "description": "d", "hashtags": [], "reason": "r"},
        {"start": 10, "end": 45, "score": 90, "hook": "h", "title": "A", "description": "d", "hashtags": [], "reason": "r"},
    ]})
    llm = FakeLLM(["esto no es json", good])
    clips = select_highlights(_transcript(), Config(), 5, 25, 60, llm)
    assert llm.calls == 2
    assert [c["title"] for c in clips] == ["A", "B"]  # ordenados por inicio
    assert [c["n"] for c in clips] == [1, 2]
