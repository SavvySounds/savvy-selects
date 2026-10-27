"""captions: per-event copy prompts built from the database. No API key, no network.

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
        raise AssertionError("without --api nothing may reach the API")


def ns(**kw):
    base = dict(crate=False, use_ratings=True, min_rating=4, min_score=7,
                event=None, api=False, force=False)
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


# -- default: free, writes prompts, calls nothing ---------------------------


def test_default_writes_one_prompt_per_event_and_calls_nothing(env):
    cfg, con = env
    captions.run(cfg, con, ns(), client=NoCalls())

    files = sorted(p.name for p in (_out(cfg) / "prompts").glob("*.txt"))
    assert files == ["DELTA REEL.txt", "SONY HOLIDAY.txt"]
    assert not list(_out(cfg).glob("*.md")), "no copy is written without --api"
    text = (_out(cfg) / "prompts" / "DELTA REEL.txt").read_text()
    assert text.startswith(captions.VOICE), "one paste must carry the voice"
    assert "EVENT: DELTA REEL" in text


def test_prompt_carries_human_verdict_and_never_a_path(env):
    cfg, con = env
    captions.run(cfg, con, ns(), client=NoCalls())

    text = "".join(p.read_text() for p in (_out(cfg) / "prompts").glob("*.txt"))
    assert "Miles 5/5" in text and "Miles tags: hero shot" in text
    assert "road cases" not in text, "a 2-star clip is below --min-rating 4"
    assert "/Users/" not in text and ".MOV" not in text, "no paths in a prompt"


def test_errored_clip_keeps_tags_but_not_its_error_note(env):
    cfg, con = env
    con.execute("UPDATE clips SET note='overloaded_error 529' WHERE state='error'")
    con.commit()
    captions.run(cfg, con, ns(event="sony holiday"), client=NoCalls())

    text = (_out(cfg) / "prompts" / "SONY HOLIDAY.txt").read_text()
    assert "brand signage" in text
    assert "overloaded" not in text


def test_prompts_follow_the_latest_ratings(env):
    cfg, con = env
    captions.run(cfg, con, ns(), client=NoCalls())
    con.execute("UPDATE clips SET my_rating=5 WHERE note='road cases'")
    con.commit()
    captions.run(cfg, con, ns(), client=NoCalls())
    assert "road cases" in (_out(cfg) / "prompts" / "DELTA REEL.txt").read_text()


def test_unknown_event_is_refused(env):
    cfg, con = env
    with pytest.raises(SystemExit):
        captions.run(cfg, con, ns(event="NOT AN EVENT"), client=NoCalls())


# -- --api: the paid path ---------------------------------------------------


def test_api_sends_exactly_what_the_prompt_file_holds(env):
    cfg, con = env
    fake = FakeClient()
    captions.run(cfg, con, ns(api=True, event="DELTA REEL"), client=fake)

    call = fake.calls[0]
    sent = f"{call['system']}\n\n{call['messages'][0]['content']}\n"
    assert sent == (_out(cfg) / "prompts" / "DELTA REEL.txt").read_text()
    assert "## Hooks" in (_out(cfg) / "DELTA REEL.md").read_text()


def test_api_skips_events_that_already_have_copy(env):
    cfg, con = env
    fake = FakeClient()
    captions.run(cfg, con, ns(api=True), client=fake)
    captions.run(cfg, con, ns(api=True), client=fake)
    assert len(fake.calls) == 2, "second run should have nothing to send"

    captions.run(cfg, con, ns(api=True, force=True, event="DELTA REEL"), client=fake)
    assert len(fake.calls) == 3


def test_api_bad_stop_writes_nothing_so_the_next_run_retries(env):
    cfg, con = env
    captions.run(cfg, con, ns(api=True, event="DELTA REEL"),
                 client=FakeClient(stop="refusal"))
    assert not (_out(cfg) / "DELTA REEL.md").exists()

    fake = FakeClient()
    captions.run(cfg, con, ns(api=True, event="DELTA REEL"), client=fake)
    assert len(fake.calls) == 1
    assert (_out(cfg) / "DELTA REEL.md").exists()


def test_cli_wires_captions():
    args = cli.build_parser().parse_args(["captions", "--crate", "--event", "DELTA REEL"])
    assert args.crate and not args.api and args.event == "DELTA REEL"
    assert cli.build_parser().parse_args(["captions", "--api"]).api
    assert cli.COMMANDS["captions"] is captions.run
