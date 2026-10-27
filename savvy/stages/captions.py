"""Stage 7: draft Instagram copy per event from what the pipeline already knows.

Text in, text out. Claude never sees footage here - only the event name and the
tags, notes and ratings already in the database - so this costs cents per
event, needs no drive mounted, and cannot trigger a Dropbox download.

One markdown file per event under SELECTS/CAPTIONS. An event that already has a
file is skipped, so a killed run picks up where it stopped.
"""
import datetime
import os

from .. import config
from ..voice import VOICE
from .export import _safe, _select, clip_stem

# The list is best-first; past this the tail adds tokens, not ideas.
MAX_SHOTS = 30


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
    """Everything that leaves the machine for one event. Never a path."""
    return "\n".join([f"EVENT: {event}", f"SHOTS ({len(rows)}, best first):"]
                     + [_shot_line(r) for r in rows])


def _shot_key(rows):
    lines = ["| id | export file | yours | grader | tags |", "|---|---|---|---|---|"]
    for r in rows:
        tags = r["my_tags"] or r["tags"] or ""
        lines.append(f"| {r['id']:05d} | {clip_stem(r)}.mp4 | {r['my_rating'] or ''}"
                     f" | {r['score'] if r['score'] is not None else ''} | {tags} |")
    return "\n".join(lines)


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
    print(f"Drafting copy from {label}.")
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
    todo = []
    for event, rows in events.items():
        path = out_dir / f"{_safe(event)}.md"
        if path.exists() and not args.force:
            print(f"  have {path.name}, skipping (--force to redo)")
            continue
        todo.append((event, rows[:MAX_SHOTS], path))

    if args.dry_run:
        for event, rows, path in todo:
            print(f"\n--- would send for {event} -> {path.name}\n{brief(event, rows)}")
        print(f"\nDry run. {len(todo)} events, nothing sent.")
        return
    if not todo:
        print("Every selected event already has copy.")
        return

    client = client or _client()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Drafting copy for {len(todo)} events with {cfg['copy_model']}.")
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
        # Writing nothing on a bad stop leaves the event in todo for the next run.
        if msg.stop_reason != "end_turn":
            print(f"  ! {event}: stopped on {msg.stop_reason}, nothing written")
            continue

        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        path.write_text(
            f"# {event}\n\n"
            f"Drafted {datetime.date.today().isoformat()} from {len(rows)} shots"
            f" ({label}). Read it before you post it.\n\n"
            f"{text}\n\n---\n\n## Shot key\n\n{_shot_key(rows)}\n")
        done += 1
        print(f"  [{i}/{len(todo)}] {path.name}")

    print(f"\nDone. {done} of {len(todo)} drafted, {tok_in:,} tokens in / "
          f"{tok_out:,} out.\nCopy in {out_dir}")
