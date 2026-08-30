"""Cloud-only (Dropbox placeholder) handling.

The archive lives in Dropbox with most files online-only: full size on paper,
no bytes on disk. Opening one makes Dropbox download it. A full proxy run over
790 GB of placeholders would pull the lot onto a disk with far less free space,
unattended, overnight. So placeholders are parked, exactly like .insv files, and
only fetched deliberately.
"""
import argparse
import os
from pathlib import Path

import pytest

from savvy import db, storage
from savvy.stages import proxy


def ns(**kw):
    return argparse.Namespace(**kw)


class FakeStat:
    """Enough of os.stat_result to exercise the placeholder test."""

    def __init__(self, size, blocks, flags=0):
        self.st_size = size
        self.st_blocks = blocks
        self.st_flags = flags


# --------------------------------------------------------------------------
# detection


def test_zero_blocks_with_real_size_is_cloud_only():
    assert storage.is_cloud_only(FakeStat(size=210063969, blocks=0))


def test_dataless_flag_is_cloud_only_even_when_blocks_look_fine():
    """Belt and braces: macOS sets SF_DATALESS on File Provider placeholders."""
    assert storage.is_cloud_only(FakeStat(size=1000, blocks=8, flags=storage.SF_DATALESS))


def test_materialised_file_is_not_cloud_only():
    assert not storage.is_cloud_only(FakeStat(size=1000, blocks=8))


def test_empty_file_is_not_cloud_only():
    """A genuinely empty file also has no blocks. It is not a placeholder."""
    assert not storage.is_cloud_only(FakeStat(size=0, blocks=0))


def test_real_local_file_reads_as_local(tmp_path):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"x" * 4096)
    assert not storage.is_cloud_only(p.lstat())
    assert storage.probe(p) == (4096, False)


def test_probe_never_opens_the_file(tmp_path, monkeypatch):
    """Opening a placeholder is what triggers the download. probe must not."""
    p = tmp_path / "a.mp4"
    p.write_bytes(b"x" * 10)

    def boom(*a, **kw):
        raise AssertionError("probe opened the file, which would trigger a download")

    monkeypatch.setattr("builtins.open", boom)
    storage.probe(p)


# --------------------------------------------------------------------------
# survey


def test_survey_splits_local_from_cloud_per_event(tmp_path, monkeypatch):
    root = tmp_path / "src"
    (root / "DELTA REEL").mkdir(parents=True)
    (root / "NYE").mkdir()
    local = root / "DELTA REEL" / "a.mp4"
    local.write_bytes(b"x" * 100)
    cloudy = root / "DELTA REEL" / "b.mp4"
    cloudy.write_bytes(b"x" * 900)
    other = root / "NYE" / "c.mov"
    other.write_bytes(b"x" * 50)

    monkeypatch.setattr(storage, "is_cloud_only", lambda st: st.st_size == 900)

    events = {e["event"]: e for e in storage.survey([root], [".mp4", ".mov"])}
    assert events["DELTA REEL"]["local_files"] == 1
    assert events["DELTA REEL"]["cloud_files"] == 1
    assert events["DELTA REEL"]["cloud_bytes"] == 900
    assert events["NYE"]["cloud_files"] == 0


def test_survey_ignores_non_video(tmp_path):
    root = tmp_path / "src"
    (root / "EVENT").mkdir(parents=True)
    (root / "EVENT" / "notes.txt").write_bytes(b"hello")
    assert storage.survey([root], [".mp4"]) == []


# --------------------------------------------------------------------------
# proxy parks placeholders


@pytest.fixture
def cloudy_env(tmp_path, monkeypatch):
    """One local video and one placeholder, in the same event folder."""
    from savvy import config

    root = tmp_path / "src"
    ev = root / "DELTA REEL"
    ev.mkdir(parents=True)
    import subprocess
    real = ev / "local.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=24:duration=2", "-pix_fmt",
                    "yuv420p", str(real)], check=True)
    placeholder = ev / "cloud.mp4"
    placeholder.write_bytes(b"never read me")

    monkeypatch.setattr(storage, "is_cloud_only_path",
                        lambda p: Path(p).name == "cloud.mp4")

    cfg = dict(config.DEFAULTS)
    cfg.update({"sources": [str(root)], "work_dir": str(tmp_path / "work"),
                "hwaccel": False, "proxy_height": 180})
    con = db.connect(cfg)
    yield cfg, con, real, placeholder
    con.close()


def states(con):
    return {Path(r["src_path"]).name: r["state"]
            for r in con.execute("SELECT src_path, state FROM media")}


def test_proxy_parks_cloud_only_and_proxies_the_local_one(cloudy_env):
    cfg, con, real, placeholder = cloudy_env
    proxy.run(cfg, con, ns())
    s = states(con)
    assert s["local.mp4"] == "proxied"
    assert s["cloud.mp4"] == "cloud_only", "placeholders must never be transcoded"


def test_proxy_never_transcodes_a_placeholder(cloudy_env, monkeypatch):
    """The download-triggering call must not happen for a parked file."""
    cfg, con, real, placeholder = cloudy_env
    from savvy import media
    seen = []
    orig = media.transcode
    monkeypatch.setattr(media, "transcode",
                        lambda src, *a, **kw: (seen.append(Path(src).name), orig(src, *a, **kw))[1])
    proxy.run(cfg, con, ns())
    assert "cloud.mp4" not in seen


def test_parked_file_is_picked_up_once_it_becomes_local(cloudy_env, monkeypatch):
    """The whole point of fetch: park now, process after it lands."""
    cfg, con, real, placeholder = cloudy_env
    proxy.run(cfg, con, ns())
    assert states(con)["cloud.mp4"] == "cloud_only"

    # it arrives: same path, now real footage
    import shutil
    shutil.copy(real, placeholder)
    monkeypatch.setattr(storage, "is_cloud_only_path", lambda p: False)

    proxy.run(cfg, con, ns())
    assert states(con)["cloud.mp4"] == "proxied", \
        "a file already indexed as cloud_only must not be skipped forever"


def test_proxy_reports_what_is_parked(cloudy_env, capsys):
    cfg, con, _, _ = cloudy_env
    proxy.run(cfg, con, ns())
    out = capsys.readouterr().out
    # not just the word "cloud", which the fixture's filename contains anyway
    assert "online-only" in out, "a silent skip looks like the file was processed"
    assert "savvy fetch" in out, "say how to get the parked files"


# --------------------------------------------------------------------------
# fetch


@pytest.fixture
def fetch_env(cloudy_env):
    cfg, con, real, placeholder = cloudy_env
    proxy.run(cfg, con, ns())
    return cfg, con, real, placeholder


def test_fetch_dry_run_downloads_nothing(fetch_env, monkeypatch):
    from savvy.stages import fetch
    cfg, con, _, _ = fetch_env
    monkeypatch.setattr(storage, "materialize",
                        lambda p, **kw: pytest.fail("dry run must not download"))
    fetch.run(cfg, con, ns(event=None, max_gb=0.0, dry_run=True))
    assert states(con)["cloud.mp4"] == "cloud_only"


def test_fetch_refuses_when_it_would_not_fit_on_disk(fetch_env, monkeypatch):
    from savvy.stages import fetch
    cfg, con, _, _ = fetch_env
    monkeypatch.setattr(storage, "free_bytes", lambda p: 1)  # a byte of headroom
    monkeypatch.setattr(storage, "materialize",
                        lambda p, **kw: pytest.fail("must not download without room"))
    with pytest.raises(SystemExit):
        fetch.run(cfg, con, ns(event=None, max_gb=0.0, dry_run=False))


def test_fetch_stops_at_the_budget(fetch_env, monkeypatch):
    from savvy.stages import fetch
    cfg, con, _, _ = fetch_env
    monkeypatch.setattr(storage, "materialize",
                        lambda p, **kw: pytest.fail("budget of zero must fetch nothing"))
    fetch.run(cfg, con, ns(event=None, max_gb=0.000000001, dry_run=False))


def test_fetch_flips_state_so_proxy_will_pick_it_up(fetch_env, monkeypatch):
    from savvy.stages import fetch
    cfg, con, _, placeholder = fetch_env
    monkeypatch.setattr(storage, "materialize", lambda p, **kw: Path(p).stat().st_size)
    monkeypatch.setattr(storage, "is_cloud_only_path", lambda p: False)
    fetch.run(cfg, con, ns(event=None, max_gb=100.0, dry_run=False))
    assert states(con)["cloud.mp4"] == "found"


def test_fetch_only_touches_the_named_event(fetch_env, monkeypatch):
    from savvy.stages import fetch
    cfg, con, _, _ = fetch_env
    monkeypatch.setattr(storage, "materialize",
                        lambda p, **kw: pytest.fail("wrong event was fetched"))
    fetch.run(cfg, con, ns(event="SOME OTHER EVENT", max_gb=100.0, dry_run=False))
    assert states(con)["cloud.mp4"] == "cloud_only"


def test_fetch_does_not_mark_success_when_the_file_never_landed(fetch_env, monkeypatch):
    """Dropbox can hand back a read without the file actually being local."""
    from savvy.stages import fetch
    cfg, con, _, _ = fetch_env
    monkeypatch.setattr(storage, "materialize", lambda p, **kw: 0)
    monkeypatch.setattr(storage, "is_cloud_only_path", lambda p: True)  # still not here
    fetch.run(cfg, con, ns(event=None, max_gb=100.0, dry_run=False))
    assert states(con)["cloud.mp4"] == "cloud_only", \
        "claiming success would make proxy try to open a file that is not there"


# --------------------------------------------------------------------------
# export must not download either


@pytest.fixture
def export_env(cloudy_env):
    """A scored clip whose original has gone online-only since it was proxied."""
    from savvy.stages import scan
    cfg, con, real, _ = cloudy_env
    proxy.run(cfg, con, ns())
    scan.run(cfg, con, ns())
    con.execute("UPDATE clips SET state='scored', score=9 WHERE state='candidate'")
    con.commit()
    return cfg, con, real


def test_export_skips_originals_that_are_in_the_cloud(export_env, monkeypatch):
    """exists() is true for a placeholder, so cut() would trigger a download."""
    from savvy.stages import export
    from savvy import media
    cfg, con, real = export_env
    monkeypatch.setattr(storage, "is_cloud_only_path", lambda p: True)
    monkeypatch.setattr(media, "cut",
                        lambda *a, **kw: pytest.fail("export opened a cloud original"))
    export.run(cfg, con, ns(crate=False, use_ratings=False, min_rating=4,
                            min_score=7, limit=0))
    assert not list((Path(cfg["work_dir"]) / "SELECTS" / "16x9").glob("*.mp4"))


def test_export_says_how_to_get_the_missing_originals(export_env, monkeypatch, capsys):
    from savvy.stages import export
    cfg, con, real = export_env
    monkeypatch.setattr(storage, "is_cloud_only_path", lambda p: True)
    export.run(cfg, con, ns(crate=False, use_ratings=False, min_rating=4,
                            min_score=7, limit=0))
    out = capsys.readouterr().out
    assert "savvy fetch" in out


def test_export_still_works_when_originals_are_local(export_env, monkeypatch):
    from savvy.stages import export
    cfg, con, real = export_env
    monkeypatch.setattr(storage, "is_cloud_only_path", lambda p: False)
    export.run(cfg, con, ns(crate=False, use_ratings=False, min_rating=4,
                            min_score=7, limit=0))
    assert list((Path(cfg["work_dir"]) / "SELECTS" / "16x9").glob("*.mp4"))


def test_materialize_reads_the_whole_file(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"x" * 5000)
    assert storage.materialize(p) == 5000


def test_free_bytes_is_positive(tmp_path):
    assert storage.free_bytes(tmp_path) > 0
