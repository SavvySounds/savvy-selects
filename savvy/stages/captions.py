"""Stage 7: Instagram copy per event, from what the pipeline already knows.

By default this calls nothing and costs nothing. It writes one prompt per event
- the brand voice plus the shot list - for whichever AI Miles already pays for:
paste it into Claude or ChatGPT, or have Claude Code or Codex work through the
folder. `--api` sends the same text straight to Claude instead, which is billed
per use on a separate API key.

Either way only text is involved: the event name and the tags, notes and
ratings already in the database. No footage, no paths, no drive needed, so it
cannot trigger a Dropbox download.
"""
import datetime
import os

from .. import config
from ..voice import VOICE
from .export import _safe, _select

# The list is best-first; past this the tail adds tokens, not ideas.
MAX_SHOTS = 30

ID_NOTE = "Shot numbers match the end of each exported clip's filename."


def _shot_line(row):
    parts = [f"{row['id']:05d}", f"{row['end'] - row['start']:.1f}s"]
    if row["shot"]:
        parts.append(row["shot"])
    if row["my_rating"]:
        parts.append(f"Miles {row['my_rating']}/5")
    if row["my_tags"]:
        parts.append(f"Miles tags: {row['my_tags']}")
    if row["tags"]:
        parts.append(f"grader tags: {row['tags']}")
    # An errored clip's note is the API error, not a description of the shot.
    if row["note"] and row["state"] != "error":
        parts.append(f"note: {row['note']}")
    return "- " + " | ".join(parts)


def brief(event, rows):
    """The event's shot list. Never a path."""
    return "\n".join([f"EVENT: {event}", f"SHOTS ({len(rows)}, best first):"]
                     + [_shot_line(r) for r in rows])


def prompt(event, rows):
    """One paste for any AI: exactly what --api sends as system + message."""
    return f"{VOICE}\n\n{brief(event, rows)}\n"


def _label(args):
    if args.crate:
        return "the reel crate"
    if args.use_ratings:
        return f"clips you rated {args.min_rating}+"
    return f"clips the grader scored {args.min_score}+"


def _client():
    try:
        from anthropic import Anthropic
    except ImportError:
        raise SystemExit("pip install anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY first.")
    return Anthropic()


def run(cfg, con, args, client=None):
    # The same selection export uses, so copy matches the clips you cut.
    sql, params, _ = _select(con, args)
    label = _label(args)
    print(f"Writing copy prompts from {label}.")
    events = {}
    for row in con.execute(sql, params).fetchall():
        events.setdefault(row["event"] or "misc", []).append(row)

    if args.event:
        want = args.event.strip().lower()
        events = {e: rows for e, rows in events.items() if e.lower() == want}
        if not events:
            raise SystemExit(f"No selected clips under {args.event!r}.")
    if not events:
        print("Nothing selected. Rate or flag clips in review first.")
        return

    out_dir = config.work_dir(cfg) / "SELECTS" / "CAPTIONS"
    prompt_dir = out_dir / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for event, rows in events.items():
        rows = rows[:MAX_SHOTS]
        name = _safe(event)
        # Rewritten every run: it's free, and it should follow your latest ratings.
        (prompt_dir / f"{name}.txt").write_text(prompt(event, rows))
        print(f"  prompts/{name}.txt  ({len(rows)} shots)")
        jobs.append((event, rows, out_dir / f"{name}.md"))

    if not args.api:
        print(f"\n{len(jobs)} prompts in {prompt_dir}\n"
              "Paste one into Claude or ChatGPT, or ask Claude Code or Codex:\n"
              '  "Follow each prompt in SELECTS/CAPTIONS/prompts and save the copy\n'
              '   as SELECTS/CAPTIONS/<event name>.md"\n'
              f"{ID_NOTE}")
        return
    _draft(cfg, jobs, label, args.force, client)


def _draft(cfg, jobs, label, force, client):
    """--api: send each prompt to Claude and save the answer. Billed per use."""
    out_dir = config.work_dir(cfg) / "SELECTS" / "CAPTIONS"
    todo = []
    for event, rows, path in jobs:
        if path.exists() and not force:
            print(f"  have {path.name}, skipping (--force to redo)")
            continue
        todo.append((event, rows, path))
    if not todo:
        print("Every selected event already has copy.")
        return

    client = client or _client()
    print(f"\nDrafting {len(todo)} events with {cfg['copy_model']}, "
          f"billed to your API key.")
    done, tok_in, tok_out = 0, 0, 0
    for i, (event, rows, path) in enumerate(todo, 1):
        try:
            msg = client.messages.create(
                model=cfg["copy_model"], max_tokens=16000,
                output_config={"effort": "high"},
                system=VOICE,
                messages=[{"role": "user", "content": brief(event, rows)}])
        except Exception as e:
            print(f"  ! {event}: {str(e)[:200]}")
            continue
        tok_in += msg.usage.input_tokens
        tok_out += msg.usage.output_tokens
        # Writing nothing on a bad stop leaves the event to retry next run.
        if msg.stop_reason != "end_turn":
            print(f"  ! {event}: stopped on {msg.stop_reason}, nothing written")
            continue

        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        path.write_text(
            f"# {event}\n\n"
            f"Drafted {datetime.date.today().isoformat()} from {len(rows)} shots"
            f" ({label}). {ID_NOTE} Read it before you post it.\n\n{text}\n")
        done += 1
        print(f"  [{i}/{len(todo)}] {path.name}")

    print(f"\nDone. {done} of {len(todo)} drafted, {tok_in:,} tokens in / "
          f"{tok_out:,} out.\nCopy in {out_dir}")
