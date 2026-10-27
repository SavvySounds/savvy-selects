"""captions: per-event copy drafted from the database. No API key, no network.

No footage either. The stage only reads tags, notes and ratings, so these tests
seed the database directly - which is itself the point: if captions ever needed
a file on disk, it could trigger a Dropbox download.
"""
import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from savvy import cli, config, db  # noqa: E402
from savvy.stages import captions  # noqa: E402


class FakeClient:
    """Stands in for anthropic.Anthropic(); records every request."""

    def __init__(self, stop="end_turn"):
        self.calls = []
        self.stop = stop
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            stop_reason=self.stop,
            usage=SimpleNamespace(input_tokens=900, output_tokens=1200),
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text="## Hooks\n- Read the room.")])


class NoCalls:
    @property
    def messages(self):
        raise AssertionError("a dry run must never reach the API")


def ns(**kw):
    base = dict(crate=False, use_ratings=True, min_rating=4, min_score=7,
                event=None, force=False, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def env(tmp_path):
    cfg = dict(config.DEFAULTS)
    cfg.update({"sources": ["/Users/miles/Dropbox/Content by Event"],
                "work_dir": str(tmp_path / "work")})
    con = db.connect(cfg)
    clips = [
        # event, rating, my_tags, grader tags, note, state
        ("DELTA REEL", 5, "hero shot", "packed floor,wide crowd", "full room at peak", "scored"),
        ("DELTA REEL", 4, None, "dj in frame,uplighting", "dj working the decks", "scored"),
        ("DELTA REEL", 2, None, "load-in", "road cases", "scored"),
        ("SONY HOLIDAY", 4, None, "brand signage", None, "error"),
    ]
    for n, (event, rating, mine, tags, note, state) in enumerate(clips):
        src = f"/Users/miles/Dropbox/Content by Event/{event}/IMG_{n:04d}.MOV"
        cur = con.execute(
            "INSERT INTO media (src_path, event, state, proxy_path) VALUES (?,?,?,?)",
            (src, event, "proxied", f"/Users/miles/SavvySelects/proxies/{n}.mp4"))
        con.execute(
            "INSERT INTO clips (media_id, start, end, state, score, tags, shot, note,"
            " my_rating, my_tags) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cur.lastrowid, 1.0, 5.5, state, 8, tags, "wide", note, rating, mine))
    con.commit()
    yield cfg, con
    con.close()


def _out(cfg):
    return Path(cfg["work_dir"]) / "SELECTS" / "CAPTIONS"


def test_one_file_per_event_with_a_shot_key(env):
    cfg, con = env
    fake = FakeClient()
    captions.run(cfg, con, ns(), client=fake)

    assert len(fake.calls) == 2
    files = sorted(p.name for p in _out(cfg).glob("*.md"))
    assert files == ["DELTA REEL.md", "SONY HOLIDAY.md"]
    text = (_out(cfg) / "DELTA REEL.md").read_text()
    assert "## Hooks" in text
    assert "10_DELTA REEL_00001.mp4" in text, "shot key must name the file export cuts"


def test_brief_carries_human_verdict_and_never_a_path(env):
    cfg, con = env
    fake = FakeClient()
    captions.run(cfg, con, ns(), client=fake)

    sent = "\n".join(c["messages"][0]["content"] for c in fake.calls)
    assert "Miles 5/5" in sent and "Miles tags: hero shot" in sent
    assert "road cases" not in sent, "a 2-star clip is below --min-rating 4"
    assert "/Users/" not in sent and ".MOV" not in sent, "no paths leave the machine"
    assert all(c["system"] == captions.VOICE for c in fake.calls)


def test_errored_clip_sends_tags_but_not_its_error_note(env):
    cfg, con = env
    con.execute("UPDATE clips SET note='overloaded_error 529' WHERE state='error'")
    con.commit()
    fake = FakeClient()
    captions.run(cfg, con, ns(event="sony holiday"), client=fake)

    sent = fake.calls[0]["messages"][0]["content"]
    assert "brand signage" in sent
    assert "overloaded" not in sent


def test_rerun_skips_events_that_already_have_copy(env):
    cfg, con = env
    fake = FakeClient()
    captions.run(cfg, con, ns(), client=fake)
    captions.run(cfg, con, ns(), client=fake)
    assert len(fake.calls) == 2, "second run should have nothing to do"

    captions.run(cfg, con, ns(force=True, event="DELTA REEL"), client=fake)
    assert len(fake.calls) == 3


def test_dry_run_sends_nothing_and_writes_nothing(env, capsys):
    cfg, con = env
    captions.run(cfg, con, ns(dry_run=True), client=NoCalls())
    assert not _out(cfg).exists() or not list(_out(cfg).glob("*.md"))
    out = capsys.readouterr().out
    assert "EVENT: DELTA REEL" in out and "nothing sent" in out


def test_a_bad_stop_writes_nothing_so_the_next_run_retries(env):
    cfg, con = env
    captions.run(cfg, con, ns(event="DELTA REEL"), client=FakeClient(stop="refusal"))
    assert not (_out(cfg) / "DELTA REEL.md").exists()

    fake = FakeClient()
    captions.run(cfg, con, ns(event="DELTA REEL"), client=fake)
    assert len(fake.calls) == 1
    assert (_out(cfg) / "DELTA REEL.md").exists()


def test_unknown_event_is_refused(env):
    cfg, con = env
    with pytest.raises(SystemExit):
        captions.run(cfg, con, ns(event="NOT AN EVENT"), client=FakeClient())


def test_cli_wires_captions():
    args = cli.build_parser().parse_args(
        ["captions", "--crate", "--event", "DELTA REEL", "--dry-run"])
    assert args.crate and args.dry_run and args.event == "DELTA REEL"
    assert cli.COMMANDS["captions"] is captions.run
