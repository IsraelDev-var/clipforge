import json
import time

import pytest
from fastapi.testclient import TestClient

from clipforge.job import create_job
from clipforge.web import app as webapp


@pytest.fixture()
def env(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(webapp, "run_pipeline", lambda job, cfg, frm=None: calls.append(("run", job.id, frm)))
    monkeypatch.setattr(webapp, "rerender_clip", lambda job, cfg, n, s, e, cut=None: calls.append(("render", n, s, e)))
    job = create_job("https://www.youtube.com/watch?v=abc123", {"clips": 2, "min": 20, "max": 40}, tmp_path, "demo")
    words = [{"w": "hola", "s": 1.0, "e": 1.4}, {"w": "mundo.", "s": 1.5, "e": 2.0}]
    job.save_json("transcript.json", {"segments": [{"id": 0, "start": 1, "end": 2, "text": "", "words": words}]})
    job.save_json("highlights.json", {"clips": [
        {"n": 1, "start": 1.0, "end": 2.0, "score": 80, "title": "Uno", "description": "d", "hashtags": ["#a"],
         "hook": "h", "reason": "r"}]})
    job.path("clip_1.mp4").write_bytes(b"fake")
    job.set_status("done")
    client = TestClient(webapp.create_app(tmp_path))
    return client, calls, tmp_path


def wait(calls, n=1):
    for _ in range(100):
        if len(calls) >= n:
            return
        time.sleep(0.02)


def test_list_and_detail(env):
    client, _, _ = env
    jobs = client.get("/api/jobs").json()
    assert [j["id"] for j in jobs] == ["demo"]
    d = client.get("/api/jobs/demo").json()
    assert d["clips"][0]["file"] == "clip_1.mp4"
    assert d["clips"][0]["text"] == "hola mundo."


def test_edit_clip_text_validates(env):
    client, _, tmp = env
    r = client.patch("/api/jobs/demo/clips/1", json={"title": "x" * 80, "hashtags": ["python", "dev tips"]})
    assert r.status_code == 200
    assert len(r.json()["title"]) <= 60 and r.json()["hashtags"] == ["#python", "#devtips"]
    clips = json.loads((tmp / "demo" / "clips.json").read_text())["clips"]
    assert clips[0]["hashtags"] == ["#python", "#devtips"]


def test_rerender_and_resume_go_to_worker(env):
    client, calls, _ = env
    assert client.post("/api/jobs/demo/clips/1/render", json={"start": 1.0, "end": 30.0}).status_code == 202
    wait(calls)
    assert client.post("/api/jobs/demo/resume", json={"from_stage": "select"}).status_code == 202
    wait(calls, 2)
    assert calls == [("render", 1, 1.0, 30.0), ("run", "demo", "select")]
    assert client.post("/api/jobs/demo/resume", json={"from_stage": "nope"}).status_code == 422


def test_new_job_validation(env):
    client, calls, _ = env
    base = {"clips": "3", "min_s": "20", "max_s": "40"}
    assert client.post("/api/jobs", data={**base, "url": "no es url"}).status_code == 422
    assert client.post("/api/jobs", data={**base, "min_s": "50", "url": "https://x.com/v"}).status_code == 422
    bad = client.post("/api/jobs", data=base, files={"file": ("a.txt", b"x")})
    assert bad.status_code == 422
    ok = client.post("/api/jobs", data=base, files={"file": ("mi directo.mp4", b"video")})
    assert ok.status_code == 201
    wait(calls)
    assert calls[0][0] == "run"


def test_files_whitelist_and_delete(env):
    client, _, tmp = env
    assert client.get("/api/jobs/demo/files/clip_1.mp4").content == b"fake"
    assert client.get("/api/jobs/demo/files/job.json").status_code == 404
    assert client.get("/api/jobs/..%2F..%2Fetc/files/clips.json").status_code in (400, 404)
    assert client.get("/api/jobs/demo/zip").status_code == 200
    assert client.delete("/api/jobs/demo").status_code == 200
    assert not (tmp / "demo").exists()


def test_interrupted_jobs_marked_on_startup(tmp_path):
    job = create_job("x.mp4", {}, tmp_path, "half")
    job.set_status("running", "transcribe")
    client = TestClient(webapp.create_app(tmp_path))
    st = client.get("/api/jobs/half").json()["status"]
    assert st["state"] == "error" and st["stage"] == "transcribe"
