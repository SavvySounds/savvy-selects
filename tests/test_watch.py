"""`savvy watch` and the Gemini client, offline.

Gemini is faked at two levels: a stand-in transport under the real client, to
pin the wire format, and a stand-in client under the command, to pin what
leaves the machine. No key, no network.
"""
import argparse
import io
import json
import subprocess
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from savvy import config, gemini, media, storage
from savvy.rubric import RUBRIC, TASTE
from savvy.stages import watch


def ns(**kw):
    base = dict(mode="report", ask=None, start=None, end=None, fps=None, model=None)
    return argparse.Namespace(**{**base, **kw})


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """Six seconds of 1080p with a tone, three shots, sitting in a 'source'."""
    d = tmp_path_factory.mktemp("src") / "DELTA REEL"
    d.mkdir()
    parts = []
    for i, src in enumerate(["testsrc2", "smptebars", "mandelbrot"]):
        p = d.parent / f"p{i}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"{src}=size=1920x1080:rate=24", "-f", "lavfi", "-i",
                        "sine=frequency=440", "-t", "2", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(p)], check=True)
        parts.append(p)
    lst = d.parent / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    out = d / "clip01.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", str(out)], check=True)
    for p in parts + [lst]:
        p.unlink()
    return out


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    c = dict(config.DEFAULTS)
    c["work_dir"] = str(tmp_path / "work")
    c["hwaccel"] = False
    return c


class FakeGemini:
    """Stands in for gemini.Client and remembers everything it was handed."""
    instances = []

    def __init__(self, key, fail_generate=None):
        self.key = key
        self.uploaded = []
        self.deleted = []
        self.calls = []
        self.fail_generate = fail_generate
        FakeGemini.instances.append(self)

    def upload(self, path):
        path = Path(path)
        self.uploaded.append({"path": path, "size": path.stat().st_size,
                              "streams": _streams(path),
                              "duration": media.probe_duration(path)})
        return {"name": "files/abc", "uri": "https://g/files/abc",
                "mimeType": "video/mp4", "state": "PROCESSING"}

    def wait_active(self, f):
        return {**f, "state": "ACTIVE"}

    def generate(self, model, parts):
        self.calls.append((model, parts))
        if self.fail_generate:
            raise self.fail_generate
        return "## Summary\nA packed floor.", {"promptTokenCount": 1200,
                                               "candidatesTokenCount": 80}

    def delete(self, name):
        self.deleted.append(name)

    def models(self):
        return ["gemini-9-flash", "gemini-9-pro", "embedding-1"]


def _streams(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height", "-of", "json", str(path)],
                       capture_output=True, text=True)
    return json.loads(r.stdout)["streams"]


@pytest.fixture
def fake(monkeypatch):
    FakeGemini.instances.clear()
    monkeypatch.setattr(watch.gemini, "Client", FakeGemini)
    return FakeGemini


def _listing(d):
    return sorted((p.name, p.stat().st_size) for p in Path(d).rglob("*"))


# --------------------------------------------------------------------------
# what leaves the machine


def test_sends_a_small_copy_never_the_original(cfg, clip, fake):
    before = _listing(clip.parent)
    out = watch.run(cfg, None, ns(video=str(clip)))
    g = fake.instances[0]

    sent = g.uploaded[0]
    assert sent["path"] != clip
    assert Path(cfg["work_dir"]) in sent["path"].parents
    video = next(s for s in sent["streams"] if s["codec_type"] == "video")
    assert video["height"] == 720, "the 1080p original went up, not the copy"
    assert any(s["codec_type"] == "audio" for s in sent["streams"]), \
        "audio dropped, so there is nothing to transcribe"
    assert sent["size"] < clip.stat().st_size

    assert _listing(clip.parent) == before, "something was written to the source"
    assert not sent["path"].exists(), "upload copy left behind in work_dir"
    assert g.deleted == ["files/abc"], "upload left sitting on Google's servers"
    assert out.parent == Path(cfg["work_dir"]) / "watch"
    assert "A packed floor." in out.read_text()


def test_remote_copy_is_deleted_even_when_the_question_fails(cfg, clip, monkeypatch):
    made = []

    def failing(key):
        g = FakeGemini(key, fail_generate=gemini.GeminiError("boom", code=400))
        made.append(g)
        return g

    monkeypatch.setattr(watch.gemini, "Client", failing)
    with pytest.raises(SystemExit, match="boom"):
        watch.run(cfg, None, ns(video=str(clip)))
    assert made[0].deleted == ["files/abc"]
    assert not list((Path(cfg["work_dir"]) / "watch").glob("*.mp4"))


def test_range_trims_before_upload(cfg, clip, fake):
    out = watch.run(cfg, None, ns(video=str(clip), start="1", end="3.5"))
    assert abs(fake.instances[0].uploaded[0]["duration"] - 2.5) < 0.3
    assert "range: 00:01-00:04 of the source" in out.read_text()


def test_watch_copy_trims_to_the_range(clip, tmp_path):
    out = tmp_path / "c.mp4"
    ok, err = media.watch_copy(clip, out, start=1.0, end=3.5, hw=False)
    assert ok, err
    assert abs(media.probe_duration(out) - 2.5) < 0.3


def test_portrait_footage_keeps_its_width(tmp_path):
    src = tmp_path / "vertical.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1080x1920:rate=24:duration=1",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    out = tmp_path / "c.mp4"
    assert media.watch_copy(src, out, hw=False)[0]
    v = _streams(out)[0]
    assert (v["width"], v["height"]) == (720, 1280)


# --------------------------------------------------------------------------
# refusals, all before anything opens the file


def test_refuses_without_a_key(cfg, clip, fake, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="GEMINI_API_KEY"):
        watch.run(cfg, None, ns(video=str(clip)))
    assert not fake.instances


def test_cloud_placeholder_is_never_opened(cfg, clip, fake, monkeypatch):
    monkeypatch.setattr(storage, "is_cloud_only_path", lambda p: True)

    def boom(*a, **kw):
        raise AssertionError("ffmpeg ran on a placeholder, which downloads it")

    monkeypatch.setattr(media, "run", boom)
    monkeypatch.setattr(media, "probe_duration", boom)
    with pytest.raises(SystemExit, match="online-only"):
        watch.run(cfg, None, ns(video=str(clip)))
    assert not fake.instances[0].uploaded


def test_refuses_a_folder(cfg, clip, fake):
    with pytest.raises(SystemExit, match="One video at a time"):
        watch.run(cfg, None, ns(video=str(clip.parent)))


def test_refuses_360_source(cfg, tmp_path, fake):
    p = tmp_path / "VID_0001.insv"
    p.write_bytes(b"not really 360 data")
    with pytest.raises(SystemExit, match="Insta360"):
        watch.run(cfg, None, ns(video=str(p)))


def test_unknown_model_lists_real_ones(cfg, clip, monkeypatch):
    monkeypatch.setattr(watch.gemini, "Client", lambda key: FakeGemini(
        key, fail_generate=gemini.GeminiError("not found", code=404)))
    with pytest.raises(SystemExit) as e:
        watch.run(cfg, None, ns(video=str(clip), model="gemini-0-nope"))
    assert "gemini-9-flash" in str(e.value) and "embedding-1" not in str(e.value)


# --------------------------------------------------------------------------
# prompts


def test_selects_judges_by_the_same_taste_as_the_grader(cfg):
    assert TASTE in RUBRIC
    assert TASTE in watch.build_prompt("selects", None, cfg)


def test_teardown_gets_the_measured_cut_list(cfg, clip, fake):
    watch.run(cfg, None, ns(video=str(clip), mode="teardown"))
    model, parts = fake.instances[0].calls[0]
    prompt = parts[1]["text"]
    assert "3 shots" in prompt, prompt
    assert parts[0]["videoMetadata"]["fps"] == watch.MODES["teardown"]


def test_unmeasured_is_not_reported_as_no_cuts(cfg):
    """A YouTube teardown has no local file to measure. Telling Gemini there
    were no cuts would be a lie it would then repeat."""
    p = watch.build_prompt("teardown", None, cfg, cuts=None)
    assert "no cuts" not in p and "estimates" in p
    assert "no cuts" in watch.build_prompt("teardown", None, cfg, cuts=[(0, 6.0)])


def test_ask_replaces_the_preset_but_keeps_the_ground_rules(cfg):
    p = watch.build_prompt("report", "Is the DJ visible?", cfg)
    assert p.startswith("Is the DJ visible?")
    assert "Never invent" in p and "## Scene by scene" not in p


def test_youtube_is_passed_by_url_and_nothing_is_uploaded(cfg, fake):
    url = "https://www.youtube.com/watch?v=dQw4w9-gXcQ"
    out = watch.run(cfg, None, ns(video=url, start="30", end="1:00"))
    g = fake.instances[0]
    assert not g.uploaded
    part = g.calls[0][1][0]
    assert part["fileData"]["fileUri"] == url
    assert part["videoMetadata"]["startOffset"] == "30s"
    assert part["videoMetadata"]["endOffset"] == "60s"
    assert out.name.startswith("youtube-dQw4w9-gXcQ-")


def test_youtube_share_link_is_sent_as_a_plain_watch_url(cfg, fake):
    watch.run(cfg, None, ns(video="https://youtu.be/QLLuZbuTIRc?is=5Pk-w6XZIcL4F35o"))
    part = fake.instances[0].calls[0][1][0]
    assert part["fileData"]["fileUri"] == "https://www.youtube.com/watch?v=QLLuZbuTIRc"


# --------------------------------------------------------------------------
# helpers


def test_parse_time():
    assert watch.parse_time("90") == 90
    assert watch.parse_time("1:30") == 90
    assert watch.parse_time("0:01:30.5") == 90.5
    assert watch.parse_time(None) is None
    with pytest.raises(SystemExit):
        watch.parse_time("ninety")


def test_long_video_drops_fps_to_fit_then_gives_up():
    assert watch.pick_fps(60, 2.0) == 2.0
    fps = watch.pick_fps(3600, 1.0)
    assert 0.1 <= fps < 1.0
    assert watch.estimate_tokens(3600, fps) <= watch.TOKEN_BUDGET
    assert watch.pick_fps(10 * 3600, 1.0) is None


# --------------------------------------------------------------------------
# the real client, on a stand-in transport


class Resp:
    def __init__(self, body=b"", headers=None):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=None):
        data = req.data.read() if hasattr(req.data, "read") else req.data
        self.requests.append((req.get_method(), req.full_url,
                              dict(req.header_items()), data))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def http_error(code, message):
    body = json.dumps({"error": {"message": message}}).encode()
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))


def test_upload_is_a_two_step_resumable_upload(tmp_path):
    f = tmp_path / "c.mp4"
    f.write_bytes(b"x" * 1234)
    t = Transport(Resp(headers={"X-Goog-Upload-URL": "https://up/session1"}),
                  Resp({"file": {"name": "files/abc", "state": "PROCESSING"}}))
    got = gemini.Client("k", opener=t).upload(f)

    assert got["name"] == "files/abc"
    (m1, u1, h1, d1), (m2, u2, h2, d2) = t.requests
    assert u1.endswith("/upload/v1beta/files")
    assert h1["X-goog-upload-command"] == "start"
    assert h1["X-goog-upload-header-content-length"] == "1234"
    assert u2 == "https://up/session1"
    assert h2["X-goog-upload-command"] == "upload, finalize"
    assert d2 == b"x" * 1234


def test_key_goes_in_a_header_never_the_url():
    t = Transport(Resp({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}))
    gemini.Client("secret-key", opener=t).generate("m", [{"text": "hi"}])
    method, url, headers, _ = t.requests[0]
    assert "secret-key" not in url
    assert headers["X-goog-api-key"] == "secret-key"


def test_waits_until_processed():
    t = Transport(Resp({"name": "files/abc", "state": "PROCESSING"}),
                  Resp({"name": "files/abc", "state": "ACTIVE", "uri": "u"}))
    c = gemini.Client("k", opener=t, sleep=lambda s: None)
    got = c.wait_active({"name": "files/abc", "state": "PROCESSING"})
    assert got["state"] == "ACTIVE" and len(t.requests) == 2


def test_failed_processing_is_an_error():
    c = gemini.Client("k", opener=Transport(), sleep=lambda s: None)
    with pytest.raises(gemini.GeminiError, match="FAILED"):
        c.wait_active({"name": "files/abc", "state": "FAILED"})


def test_answer_skips_thinking_and_retries_when_busy():
    answer = {"candidates": [{"content": {"parts": [
        {"text": "reasoning...", "thought": True}, {"text": "The answer."}]}}],
        "usageMetadata": {"promptTokenCount": 10}}
    waits = []
    t = Transport(http_error(503, "overloaded"), Resp(answer))
    text, usage = gemini.Client("k", opener=t, sleep=waits.append).generate(
        "models/gemini-x", [{"text": "hi"}])
    assert text == "The answer."
    assert usage["promptTokenCount"] == 10
    assert len(waits) == 1
    assert t.requests[1][1].endswith("/v1beta/models/gemini-x:generateContent")


def test_a_real_error_is_not_retried_and_reads_plainly():
    t = Transport(http_error(400, "Video is too long"))
    with pytest.raises(gemini.GeminiError, match="Video is too long") as e:
        gemini.Client("k", opener=t, sleep=lambda s: None).generate("m", [])
    assert e.value.code == 400 and len(t.requests) == 1
