import json
import time
from datetime import datetime, timedelta, timezone

import httplib2
import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from clipforge import ClipForgeError
from clipforge.config import Config, YouTubeConfig
from clipforge.job import create_job
from clipforge.publish import youtube as yt

YC = YouTubeConfig()
CLIP = {"n": 1, "start": 125.7, "end": 160, "title": "El await que rompe tu API",
        "description": "Error típico en Python async", "hashtags": ["#python", "#programacion"]}


def test_metadata_adds_shorts_tag_link_and_tags():
    s = yt.build_metadata(CLIP, "https://www.youtube.com/watch?v=abcdefghijk", YC)
    assert s["title"] == "El await que rompe tu API #Shorts"
    assert "#python #programacion" in s["description"]
    assert "https://www.youtube.com/watch?v=abcdefghijk&t=125s" in s["description"]
    assert s["tags"] == ["python", "programacion"] and s["categoryId"] == "28"


def test_metadata_limits_and_forbidden_chars():
    long = {**CLIP, "title": "<b>" + "x" * 120, "description": "a < b > c"}
    s = yt.build_metadata(long, "/videos/local.mp4", YC)
    assert len(s["title"]) <= 100 and "<" not in s["title"]
    assert "#Shorts" in s["description"]  # el título no tenía sitio: va en la descripción
    assert "<" not in s["description"] and "Directo completo" not in s["description"]  # sin URL de YouTube
    edited = yt.build_metadata(CLIP, None, YC, title="Mi título #shorts", description="Texto propio")
    assert edited["title"] == "Mi título #shorts" and edited["description"] == "Texto propio"


def test_status_and_schedule():
    now = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)
    assert yt.build_status("public", None) == {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    st = yt.build_status("public", now + timedelta(days=1), now=now)
    assert st["privacyStatus"] == "private" and st["publishAt"] == "2026-12-02T10:00:00Z"
    with pytest.raises(ClipForgeError):
        yt.build_status("public", now - timedelta(hours=1), now=now)
    with pytest.raises(ClipForgeError):
        yt.build_status("secreto", None)
    times = yt.schedule_times(yt.parse_when("2026-12-01T18:00:00+01:00"), 24, 3)
    assert [t.day for t in times] == [1, 2, 3] and times[0].utcoffset() == timedelta(hours=1)
    assert yt.parse_when("2026-12-01 18:00").tzinfo is not None  # sin zona → hora local
    assert yt.video_id_from_url("https://youtu.be/abcdefghijk?t=3") == "abcdefghijk"


class FakeStatus:
    def __init__(self, p):
        self._p = p

    def progress(self):
        return self._p


class FakeRequest:
    def __init__(self, script):
        self.script = list(script)

    def next_chunk(self):
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class FakeService:
    def __init__(self, script):
        self.script = script
        self.bodies = []

    def videos(self):
        return self

    def insert(self, part, body, media_body):
        self.bodies.append(body)
        return FakeRequest(self.script)


def http_error(status, reason=""):
    return HttpError(httplib2.Response({"status": status}), json.dumps({"error": {"message": reason}}).encode())


def test_upload_progress_and_retries(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x" * 1000)
    svc = FakeService([(FakeStatus(0.5), None), http_error(503), (None, {"id": "VID123"})])
    seen, sleeps = [], []
    vid = yt.upload(svc, f, {"snippet": {}}, progress=seen.append, sleep=sleeps.append)
    assert vid == "VID123" and seen == [0.5, 1.0] and sleeps == [2]


def test_upload_quota_error_is_clear(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    with pytest.raises(ClipForgeError, match="cuota diaria"):
        yt.upload(FakeService([http_error(403, "quotaExceeded")]), f, {}, sleep=lambda s: None)


def _job(tmp_path):
    job = create_job("https://www.youtube.com/watch?v=abcdefghijk", {}, tmp_path, "demo")
    job.save_json("highlights.json", {"clips": [dict(CLIP)]})
    job.path("clip_1.mp4").write_bytes(b"video")
    job.set_status("done")
    return job


def test_publish_clip_records_result_and_blocks_duplicates(tmp_path):
    job = _job(tmp_path)
    svc = FakeService([(None, {"id": "VID123"})])
    res = yt.publish_clip(job, Config(), 1, privacy="unlisted", service=svc)
    assert res["url"] == "https://youtube.com/shorts/VID123" and res["privacy"] == "unlisted"
    assert job.load_json("highlights.json")["clips"][0]["youtube"]["id"] == "VID123"
    assert job.load_json("clips.json")["clips"][0]["youtube"]["id"] == "VID123"
    assert svc.bodies[0]["status"]["privacyStatus"] == "unlisted"
    with pytest.raises(ClipForgeError, match="ya se subió"):
        yt.publish_clip(job, Config(), 1, service=FakeService([]))


def test_web_publish_endpoint(tmp_path, monkeypatch):
    from clipforge.web import app as webapp

    _job(tmp_path)
    monkeypatch.setattr(yt, "connection_status", lambda yc: {"connected": True, "channel": {"title": "IsraDev RD"}})
    calls = []
    monkeypatch.setattr(yt, "publish_clip", lambda job, cfg, n, privacy, when, title, desc, force, progress:
                        (progress(0.5), calls.append((n, privacy, when, title))))
    client = TestClient(webapp.create_app(tmp_path))
    prev = client.get("/api/jobs/demo/clips/1/youtube").json()
    assert prev["snippet"]["title"].endswith("#Shorts")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert client.post("/api/jobs/demo/clips/1/publish", json={"publish_at": past}).status_code == 422
    when = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    r = client.post("/api/jobs/demo/clips/1/publish",
                    json={"title": "Hola", "privacy": "public", "publish_at": when.isoformat()})
    assert r.status_code == 202
    for _ in range(100):
        if calls:
            break
        time.sleep(0.02)
    assert calls == [(1, "public", when, "Hola")]
    for _ in range(100):
        if client.get("/api/jobs/demo").json()["status"]["state"] == "done":
            break
        time.sleep(0.02)
    assert client.get("/api/jobs/demo").json()["status"]["state"] == "done"
