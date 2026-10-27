"""Stage 4: cut winners out of the ORIGINAL files at full quality.

Selection order of preference: your reel crate, then your star ratings, then the
grader's score. Timecodes come from proxies but the pixels come from originals.
"""
import csv
from pathlib import Path

from .. import config, media, storage

VERTICAL_VF = "crop=ih*9/16:ih,scale=1080:1920"


def _select(con, args):
    if args.crate:
        return ("SELECT c.*, m.src_path, m.event FROM clips c JOIN media m ON m.id=c.media_id"
                " WHERE c.flagged=1 ORDER BY c.my_rating DESC, c.score DESC",
                (), "Exporting the reel crate.")
    if args.use_ratings:
        return ("SELECT c.*, m.src_path, m.event FROM clips c JOIN media m ON m.id=c.media_id"
                " WHERE c.my_rating >= ? ORDER BY c.my_rating DESC, c.score DESC",
                (args.min_rating,), f"Exporting clips you rated {args.min_rating}+.")
    return ("SELECT c.*, m.src_path, m.event FROM clips c JOIN media m ON m.id=c.media_id"
            " WHERE c.state IN ('scored','exported') AND c.score >= ?"
            " ORDER BY c.score DESC, c.sharpness DESC",
            (args.min_score,), f"Exporting clips the grader scored {args.min_score}+.")


def _safe(text, limit=40):
    return "".join(ch if ch.isalnum() or ch in " -_" else "_"
                   for ch in (text or "misc"))[:limit].strip()


def run(cfg, con, args):
    out_dir = config.work_dir(cfg) / "SELECTS"
    (out_dir / "16x9").mkdir(parents=True, exist_ok=True)
    if cfg["export_vertical"]:
        (out_dir / "9x16").mkdir(parents=True, exist_ok=True)

    sql, params, banner = _select(con, args)
    print(banner)
    rows = con.execute(sql, params).fetchall()
    if args.limit:
        rows = rows[:args.limit]
    print(f"Exporting {len(rows)} clips at full quality from originals.")

    catalog = []
    in_cloud = []
    for i, row in enumerate(rows, 1):
        src = Path(row["src_path"])
        if not src.exists():
            print(f"  ! source offline, skipping: {src}")
            continue
        # A placeholder passes exists(). Cutting one would quietly pull the whole
        # original down mid-export, which is the thing we are avoiding.
        if storage.is_cloud_only_path(src):
            in_cloud.append(row["event"])
            print(f"  ! original is online-only, skipping: {src.name}")
            continue

        rank = row["my_rating"] * 2 if row["my_rating"] else (row["score"] or 0)
        stem = f"{rank:02d}_{_safe(row['event'])}_{row['id']:05d}"

        wide = out_dir / "16x9" / f"{stem}.mp4"
        ok, err = media.cut(src, row["start"], row["end"], wide, hw=cfg["hwaccel"])
        if not ok:
            print(f"  ! encode failed: {err}")
            continue
        if cfg["export_vertical"]:
            media.cut(src, row["start"], row["end"], out_dir / "9x16" / f"{stem}.mp4",
                      vf=VERTICAL_VF, hw=cfg["hwaccel"], bitrate="8000k")

        con.execute("UPDATE clips SET state='exported',export_path=? WHERE id=?",
                    (str(wide), row["id"]))
        con.commit()
        catalog.append({
            "file": wide.name, "my_rating": row["my_rating"], "score": row["score"],
            "flagged": row["flagged"], "event": row["event"],
            "my_tags": row["my_tags"], "tags": row["tags"], "shot": row["shot"],
            "note": row["note"], "source": str(src),
            "start": round(row["start"], 2), "end": round(row["end"], 2),
        })
        print(f"  [{i}/{len(rows)}] {wide.name}")

    fields = ["file", "my_rating", "score", "flagged", "event", "my_tags", "tags",
              "shot", "note", "source", "start", "end"]
    cat_path = out_dir / "catalog.csv"
    with open(cat_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(catalog)

    print(f"\nDone. Clips in {out_dir}\nSearchable catalog: {cat_path}")

    if in_cloud:
        print(f"\n{len(in_cloud)} clips skipped because the original is not on this "
              f"disk. Bring the event down, then export again:")
        for event in sorted(set(in_cloud)):
            print(f"  savvy fetch --event \"{event}\"")
