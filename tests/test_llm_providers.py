import json

import httpx
import pytest

from clipforge import ClipForgeError
from clipforge.config import Config
from clipforge.stages import select
from clipforge.stages.select import RANK_SCHEMA, OpenAICompatLLM, make_llm


def _llm(handler, name="gemini") -> OpenAICompatLLM:
    llm = OpenAICompatLLM(name, "https://example.test/v1/", "modelo-x", "clave")
    llm.client = httpx.Client(base_url="https://example.test/v1/", transport=httpx.MockTransport(handler))
    return llm


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_sends_schema_and_returns_content():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return _ok('{"selected": [1, 0]}')

    assert _llm(handler).complete("sys", "user", RANK_SCHEMA) == '{"selected": [1, 0]}'
    assert seen["model"] == "modelo-x"
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["response_format"]["json_schema"]["schema"] == RANK_SCHEMA


def test_falls_back_to_json_object_when_schema_rejected():
    formats = []

    def handler(req):
        fmt = json.loads(req.content)["response_format"]["type"]
        formats.append(fmt)
        return httpx.Response(400, text="no schema") if fmt == "json_schema" else _ok("{}")

    llm = _llm(handler)
    assert llm.complete("sys", "user", RANK_SCHEMA) == "{}"
    llm.complete("sys", "user", RANK_SCHEMA)
    assert formats == ["json_schema", "json_object", "json_object"]


def test_retries_on_rate_limit(monkeypatch):
    monkeypatch.setattr(select.time, "sleep", lambda s: None)
    calls = iter([httpx.Response(429, headers={"retry-after": "1"}), _ok("{}")])
    assert _llm(lambda req: next(calls)).complete("s", "u", RANK_SCHEMA) == "{}"


def test_rate_limit_exhausted_gives_resume_hint(monkeypatch):
    monkeypatch.setattr(select.time, "sleep", lambda s: None)
    with pytest.raises(ClipForgeError, match="resume"):
        _llm(lambda req: httpx.Response(429)).complete("s", "u", RANK_SCHEMA)


def test_switches_to_fallback_model_and_stays(monkeypatch):
    monkeypatch.setattr(select.time, "sleep", lambda s: None)
    used = []

    def handler(req):
        model = json.loads(req.content)["model"]
        used.append(model)
        return httpx.Response(503) if model == "modelo-x" else _ok("{}")

    llm = _llm(handler)
    llm.models = ["modelo-x", "reserva"]
    assert llm.complete("s", "u", RANK_SCHEMA) == "{}"
    llm.complete("s", "u", RANK_SCHEMA)
    assert used == ["modelo-x", "modelo-x", "reserva", "reserva"]


def test_bad_key_message():
    with pytest.raises(ClipForgeError, match="inválida"):
        _llm(lambda req: httpx.Response(401)).complete("s", "u", RANK_SCHEMA)


def test_ollama_missing_model_hint():
    with pytest.raises(ClipForgeError, match="ollama pull modelo-x"):
        _llm(lambda req: httpx.Response(404), name="ollama").complete("s", "u", RANK_SCHEMA)


def test_make_llm_providers(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    llm = make_llm(Config(llm_provider="gemini"))
    assert isinstance(llm, OpenAICompatLLM) and llm.model == "gemini-3.6-flash" and len(llm.models) > 1
    assert make_llm(Config(llm_provider="gemini", llm_model="otro")).models == ["otro"]

    ollama = make_llm(Config(llm_provider="ollama", llm_model="llama3.1:8b"))
    assert ollama.model == "llama3.1:8b" and "11434" in str(ollama.client.base_url)

    with pytest.raises(ClipForgeError, match="desconocido"):
        make_llm(Config(llm_provider="nada"))


def test_gemini_without_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # sin .env
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ClipForgeError, match="GEMINI_API_KEY"):
        make_llm(Config(llm_provider="gemini"))
