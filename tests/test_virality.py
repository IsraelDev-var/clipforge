import numpy as np

from clipforge.stages.select import Candidate
from clipforge.stages.virality import compute, energy_dynamics, speech_signals


def words_at(times, last="fin."):
    ws = [{"w": "palabra", "s": s, "e": s + 0.3} for s in times]
    ws[-1]["w"] = last
    return ws


LIVELY = words_at([0.1 + i * 0.35 for i in range(85)])  # ~2.9 palabras/s, sin pausas, ~30 s
GOOD_LLM = {"hook": 9, "standalone": 8, "value": 9, "emotion": 7, "shareability": 8}


def test_signals_detect_late_start_pauses_and_question():
    ws = words_at([2.5, 2.9, 6.0, 6.4], last="seguimos")
    ws[0]["w"] = "¿Sabías"
    sig = speech_signals(ws, 10.0)
    assert sig["hook_delay"] == 2.5
    assert sig["dead_air"] > 0.5  # 2.5 s iniciales + 2.8 s de pausa + 3.3 s finales
    assert sig["ends_sentence"] == 0.0
    assert sig["question_hook"] == 1.0


def test_good_clip_scores_high_without_tips():
    v = compute({"start": 0, "end": 30, "virality_llm": GOOD_LLM}, LIVELY, energy=0.9)
    assert v["score"] >= 85 and v["grade"] == "alto"
    assert v["tips"] == []


def test_weak_clip_scores_low_with_actionable_tips():
    slow = words_at([2.4 + i * 1.2 for i in range(40)], last="y")
    weak = {"hook": 3, "standalone": 4, "value": 4, "emotion": 2, "shareability": 3}
    v = compute({"start": 0, "end": 58, "virality_llm": weak}, slow, energy=0.2)
    assert v["score"] < 50 and v["grade"] == "bajo"
    text = " ".join(v["tips"])
    for key in ("recorta el inicio", "Gancho débil", "silencio", "fin de frase", "Dura 58 s"):
        assert key in text


def test_signals_change_score_with_same_llm():
    base = {"start": 0, "end": 30, "virality_llm": GOOD_LLM}
    late = words_at([2.5 + i * 0.35 for i in range(78)])
    assert compute(base, LIVELY, 0.9)["score"] > compute(base, late, 0.9)["score"]


def test_fallback_to_general_score_for_old_clips():
    v = compute({"start": 0, "end": 30, "score": 40, "virality_llm": None}, LIVELY, 0.9)
    assert v["llm"] is None and v["llm_score"] == 40


def test_energy_dynamics_flat_vs_dynamic():
    rate = 16000
    t = np.arange(rate * 10) / rate
    tone = np.sin(2 * np.pi * 200 * t)
    flat = (tone * 8000).astype(np.int16)
    env = 0.25 + 0.75 * (np.sin(2 * np.pi * 0.4 * t) > 0)  # alterna fuerte/suave
    dynamic = (tone * env * 8000).astype(np.int16)
    assert energy_dynamics(flat, rate) < 0.05
    assert energy_dynamics(dynamic, rate) > 0.5


def test_candidate_clamps_virality_criteria():
    c = Candidate(start=0, end=30, virality={"hook": 14, "standalone": 0, "value": 7.6, "emotion": 5, "shareability": 5})
    assert (c.virality.hook, c.virality.standalone, c.virality.value) == (10, 1, 8)
