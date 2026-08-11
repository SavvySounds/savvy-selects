"""Stage 3: grade surviving shots with Claude vision. The only paid stage."""
import base64
import io
import json
import os
import time
from pathlib import Path

from .. import media
from ..rubric import RUBRIC


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1].rsplit("```", 1)[0]
    return t.strip()


def run(cfg, con, args):
    try:
        from anthropic import Anthropic
    except ImportError:
        raise SystemExit("pip install anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY first.")

    client = Anthropic()
    rows = con.execute(
        "SELECT c.*, m.proxy_path FROM clips c JOIN media m ON m.id=c.media_id"
        " WHERE c.state='candidate' ORDER BY c.sharpness DESC").fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"Scoring {len(rows)} candidates with {cfg['model']}.")

    for i, row in enumerate(rows, 1):
        strip = media.contact_strip(Path(row["proxy_path"]), row["start"], row["end"])
        if strip is None:
            con.execute("UPDATE clips SET state='rejected' WHERE id=?", (row["id"],))
            con.commit()
            continue

        buf = io.BytesIO()
        strip.save(buf, format="JPEG", quality=72)
        b64 = base64.b64encode(buf.getvalue()).decode()

        try:
            msg = client.messages.create(
                model=cfg["model"], max_tokens=300,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                                                 "media_type": "image/jpeg",
                                                 "data": b64}},
                    {"type": "text", "text": RUBRIC}]}])
            raw = _strip_fences("".join(b.text for b in msg.content if b.type == "text"))
            data = json.loads(raw)
            con.execute(
                "UPDATE clips SET state='scored',score=?,tags=?,shot=?,note=? WHERE id=?",
                (int(data.get("score", 0)), ",".join(data.get("tags", [])),
                 data.get("orientation", ""), data.get("note", ""), row["id"]))
        except Exception as e:
            con.execute("UPDATE clips SET state='error',note=? WHERE id=?",
                        (str(e)[:200], row["id"]))
            time.sleep(2)
        con.commit()
        if i % 25 == 0:
            print(f"  {i}/{len(rows)}")

    print("Scoring complete.")
