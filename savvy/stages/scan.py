"""Stage 2: split proxies into shots and reject the unusable ones locally.

This is the cost gate. Everything killed here never reaches the paid grader.
"""
from pathlib import Path

from .. import media


def run(cfg, con, args):
    rows = con.execute("SELECT * FROM media WHERE state='proxied'").fetchall()
    print(f"Scanning {len(rows)} proxies for shots.")
    kept = tossed = 0

    for i, row in enumerate(rows, 1):
        proxy = Path(row["proxy_path"]) if row["proxy_path"] else None
        duration = row["duration"] or (media.probe_duration(proxy) if proxy else 0)
        if not proxy or not proxy.exists() or duration < cfg["min_clip_seconds"]:
            con.execute("UPDATE media SET state='scanned' WHERE id=?", (row["id"],))
            con.commit()
            continue

        for start, end in media.detect_scenes(proxy, cfg["scene_threshold"], duration):
            if end - start < cfg["min_clip_seconds"]:
                continue
            end = min(end, start + cfg["max_clip_seconds"])

            mid = media.frame_at(proxy, start + (end - start) / 2)
            if mid is None:
                continue
            sharp = media.laplacian_variance(mid)
            bright = media.mean_brightness(mid)

            a = media.frame_at(proxy, start + 0.3, height=180)
            b = media.frame_at(proxy, min(end - 0.3, start + 1.3), height=180)
            motion = 0.0
            if a and b and a.size == b.size:
                import numpy as np
                motion = float(np.abs(np.asarray(a, np.float32)
                                      - np.asarray(b, np.float32)).mean())

            ok = (sharp >= cfg["sharpness_floor"]
                  and cfg["brightness_floor"] <= bright <= cfg["brightness_ceiling"])
            kept += ok
            tossed += not ok
            con.execute(
                "INSERT INTO clips (media_id,start,end,sharpness,brightness,motion,state)"
                " VALUES (?,?,?,?,?,?,?)",
                (row["id"], start, end, sharp, bright, motion,
                 "candidate" if ok else "rejected"))

        con.execute("UPDATE media SET state='scanned' WHERE id=?", (row["id"],))
        con.commit()
        print(f"  [{i}/{len(rows)}] {Path(row['src_path']).name}")

    print(f"Scan complete. {kept} candidates kept, {tossed} rejected locally (free).")
