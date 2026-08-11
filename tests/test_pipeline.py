"""End-to-end tests against synthetic footage. No API key, no network.

Fixtures build a four-shot video: sharp / black / blurred / sharp. Scan should
keep the two sharp shots and reject the other two, which is the whole cost model.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from savvy import config, db, media  # noqa: E402
from savvy.stages import export, proxy, scan  # noqa: E402


def ns(**kw):
    return argparse.Namespace(**kw)


@pytest.fixture(scope="session")
def footage(tmp_path_factory):
    """Four concatenated 4s shots with obviously different quality."""
    d = tmp_path_factory.mktemp("src")
    (d / "DELTA REEL").mkdir()
    parts = []
    specs = [
        ("a", ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=4"]),
        ("b", ["-f", "lavfi", "-i", "color=c=black:size=640x360:rate=24:duration=4"]),
        ("c", ["-f", "lavfi", "-i", "smptebars=size=640x360:rate=24:duration=4",
               "-vf", "gblur=sigma=14"]),
        ("d", ["-f", "lavfi", "-i", "mandelbrot=size=640x360:rate=24", "-t", "4"]),
    ]
    for name, args in specs:
        p = d / f"{name}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-pix_fmt", "yuv420p",
                        str(p)], check=True)
        parts.append(p)

    lst = d / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy",
                    str(d / "DELTA REEL" / "clip01.mp4")], check=True)
    (d / "DELTA REEL" / "VID_0001.insv").write_bytes(b"not really 360 data")
    for p in parts:
        p.unlink()
    lst.unlink()
    return d / "DELTA REEL"


@pytest.fixture
def env(tmp_path, footage):
    cfg = dict(config.DEFAULTS)
    cfg.update({"sources": [str(footage.parent)], "work_dir": str(tmp_path / "work"),
                "hwaccel": False, "proxy_height": 360})
    con = db.connect(cfg)
    yield cfg, con
    con.close()


# --------------------------------------------------------------------------


def test_ffmpeg_present():
    assert media.run(["ffmpeg", "-version"]).returncode == 0


def test_event_name_from_source_root(footage):
    got = media.event_name_from(footage / "clip01.mp4", [footage.parent])
    assert got == "DELTA REEL"


def test_laplacian_separates_sharp_from_blurred(env):
    from PIL import Image, ImageFilter
    sharp = Image.effect_noise((200, 200), 60).convert("RGB")
    blurred = sharp.filter(ImageFilter.GaussianBlur(6))
    assert media.laplacian_variance(sharp) > media.laplacian_variance(blurred) * 5


def test_proxy_indexes_and_parks_insta360(env):
    cfg, con = env
    proxy.run(cfg, con, ns())
    rows = {Path(r["src_path"]).suffix: r["state"]
            for r in con.execute("SELECT src_path, state FROM media")}
    assert rows[".mp4"] == "proxied"
    assert rows[".insv"] == "needs_reframe", "360 files must never be transcoded"


def test_proxy_is_smaller_than_source(env):
    cfg, con = env
    proxy.run(cfg, con, ns())
    row = con.execute("SELECT src_path, proxy_path FROM media"
                      " WHERE state='proxied'").fetchone()
    assert Path(row["proxy_path"]).stat().st_size < Path(row["src_path"]).stat().st_size


def test_proxy_is_idempotent(env):
    cfg, con = env
    proxy.run(cfg, con, ns())
    before = con.execute("SELECT COUNT(*) n FROM media").fetchone()["n"]
    proxy.run(cfg, con, ns())
    assert con.execute("SELECT COUNT(*) n FROM media").fetchone()["n"] == before


def test_scan_rejects_black_and_blurred_shots(env):
    cfg, con = env
    proxy.run(cfg, con, ns())
    scan.run(cfg, con, ns())
    states = [r["state"] for r in con.execute("SELECT state FROM clips")]
    assert states.count("candidate") >= 2, "sharp shots should survive"
    assert states.count("rejected") >= 2, "black and blurred shots should not"


def test_export_prefers_human_rating_over_grader_score(env):
    cfg, con = env
    proxy.run(cfg, con, ns())
    scan.run(cfg, con, ns())
    con.execute("UPDATE clips SET state='scored', score=3 WHERE state='candidate'")
    cid = con.execute("SELECT id FROM clips WHERE state='scored'").fetchone()["id"]
    con.execute("UPDATE clips SET my_rating=5 WHERE id=?", (cid,))
    con.commit()

    export.run(cfg, con, ns(crate=False, use_ratings=True, min_rating=4,
                            min_score=7, limit=0))
    out = list((Path(cfg["work_dir"]) / "SELECTS" / "16x9").glob("*.mp4"))
    assert len(out) == 1
    assert out[0].name.startswith("10_"), "filename should rank by your 5 stars, not score 3"


def test_export_writes_catalog_with_both_verdicts(env):
    cfg, con = env
    proxy.run(cfg, con, ns())
    scan.run(cfg, con, ns())
    con.execute("UPDATE clips SET state='scored', score=9, tags='packed floor'"
                " WHERE state='candidate'")
    con.commit()
    export.run(cfg, con, ns(crate=False, use_ratings=False, min_rating=4,
                            min_score=7, limit=0))
    csv_path = Path(cfg["work_dir"]) / "SELECTS" / "catalog.csv"
    header = csv_path.read_text().splitlines()[0]
    for col in ("my_rating", "score", "flagged", "source", "start", "end"):
        assert col in header


def test_crate_export_only_takes_flagged(env):
    cfg, con = env
    proxy.run(cfg, con, ns())
    scan.run(cfg, con, ns())
    con.execute("UPDATE clips SET state='scored', score=9 WHERE state='candidate'")
    cid = con.execute("SELECT id FROM clips WHERE state='scored'").fetchone()["id"]
    con.execute("UPDATE clips SET flagged=1 WHERE id=?", (cid,))
    con.commit()
    export.run(cfg, con, ns(crate=True, use_ratings=False, min_rating=4,
                            min_score=7, limit=0))
    assert len(list((Path(cfg["work_dir"]) / "SELECTS" / "16x9").glob("*.mp4"))) == 1


# --------------------------------------------------------------------------
# review server


@pytest.fixture
def server(env):
    from savvy.review.server import make_handler
    from http.server import ThreadingHTTPServer

    cfg, con = env
    proxy.run(cfg, con, ns())
    scan.run(cfg, con, ns())
    con.execute("UPDATE clips SET state='scored', score=8, tags='packed floor,wide'"
                " WHERE state='candidate'")
    con.commit()

    strips = Path(cfg["work_dir"]) / "filmstrips"
    strips.mkdir(parents=True, exist_ok=True)
    handler = make_handler(cfg, con, threading.Lock(), strips)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", cfg, con
    httpd.shutdown()


def test_index_page_serves(server):
    url, _, _ = server
    with urllib.request.urlopen(url + "/") as r:
        assert r.status == 200
        assert b"Savvy Review" in r.read()


def test_clips_api_shape(server):
    url, _, _ = server
    with urllib.request.urlopen(url + "/api/clips?state=all") as r:
        d = json.loads(r.read())
    assert d["clips"] and d["total"] >= 1
    assert {"id", "start", "end", "score", "filename", "event"} <= set(d["clips"][0])


def test_video_supports_byte_ranges(server):
    """Without 206 responses the browser cannot seek, so the scrubber dies."""
    url, _, con = server
    mid = con.execute("SELECT id FROM media WHERE state='scanned'").fetchone()["id"]
    req = urllib.request.Request(f"{url}/media/{mid}", headers={"Range": "bytes=100-999"})
    with urllib.request.urlopen(req) as r:
        assert r.status == 206
        assert r.headers["Content-Range"].startswith("bytes 100-999/")
        assert len(r.read()) == 900


def test_filmstrip_generated_on_demand(server):
    url, cfg, con = server
    cid = con.execute("SELECT id FROM clips WHERE state='scored'").fetchone()["id"]
    with urllib.request.urlopen(f"{url}/strip/{cid}.jpg") as r:
        assert r.status == 200 and len(r.read()) > 1000
    assert (Path(cfg["work_dir"]) / "filmstrips" / f"{cid}.jpg").exists()


def test_rating_persists(server):
    url, _, con = server
    cid = con.execute("SELECT id FROM clips WHERE state='scored'").fetchone()["id"]
    body = json.dumps({"id": cid, "my_rating": 4, "my_tags": "opener",
                       "flagged": 1}).encode()
    req = urllib.request.Request(f"{url}/api/rate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        assert json.loads(r.read())["ok"] is True
    row = con.execute("SELECT my_rating,my_tags,flagged,reviewed_at FROM clips"
                      " WHERE id=?", (cid,)).fetchone()
    assert (row["my_rating"], row["my_tags"], row["flagged"]) == (4, "opener", 1)
    assert row["reviewed_at"]


def test_todo_filter_excludes_rated(server):
    url, _, con = server
    cid = con.execute("SELECT id FROM clips WHERE state='scored'").fetchone()["id"]
    with urllib.request.urlopen(url + "/api/clips?state=todo") as r:
        before = len(json.loads(r.read())["clips"])
    con.execute("UPDATE clips SET my_rating=3 WHERE id=?", (cid,))
    con.commit()
    with urllib.request.urlopen(url + "/api/clips?state=todo") as r:
        assert len(json.loads(r.read())["clips"]) == before - 1
