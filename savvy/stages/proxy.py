"""Stage 1: index every source file and build small proxies.

Nothing downstream ever reads an original except the final export. This is what
makes a 2 TB archive analysable overnight instead of over a weekend.
"""
from pathlib import Path

from .. import config, media


def run(cfg, con, args):
    sources = [Path(s).expanduser() for s in cfg["sources"]]
    if not sources:
        raise SystemExit("No sources configured. Add folders to config.json.")

    vid_ext = {e.lower() for e in cfg["video_ext"]}
    ins_ext = {e.lower() for e in cfg["insta360_ext"]}
    proxy_dir = config.work_dir(cfg) / "proxies"
    proxy_dir.mkdir(parents=True, exist_ok=True)

    found = 0
    for root in sources:
        if not root.exists():
            print(f"  ! source missing, skipping: {root}")
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.name.startswith("."):
                continue
            ext = p.suffix.lower()
            event = media.event_name_from(p, sources)
            if ext in ins_ext:
                con.execute(
                    "INSERT OR IGNORE INTO media (src_path,event,bytes,state,note)"
                    " VALUES (?,?,?,'needs_reframe',?)",
                    (str(p), event, p.stat().st_size,
                     "360 source - reframe to flat video in Insta360 Studio first"))
                found += 1
            elif ext in vid_ext:
                con.execute(
                    "INSERT OR IGNORE INTO media (src_path,event,bytes) VALUES (?,?,?)",
                    (str(p), event, p.stat().st_size))
                found += 1
    con.commit()
    print(f"Indexed {found} files.")

    rows = con.execute("SELECT * FROM media WHERE state='found'").fetchall()
    print(f"Building {len(rows)} proxies -> {proxy_dir}")

    for i, row in enumerate(rows, 1):
        src = Path(row["src_path"])
        if not src.exists():
            con.execute("UPDATE media SET state='missing' WHERE id=?", (row["id"],))
            con.commit()
            continue

        out = proxy_dir / f"{row['id']:06d}.mp4"
        if out.exists() and out.stat().st_size > 0:
            con.execute(
                "UPDATE media SET proxy_path=?,duration=?,state='proxied' WHERE id=?",
                (str(out), media.probe_duration(out), row["id"]))
            con.commit()
            continue

        ok, err = media.transcode(src, out, f"scale=-2:{cfg['proxy_height']}",
                                  cfg["hwaccel"], crf=cfg["proxy_crf"])
        if ok:
            con.execute(
                "UPDATE media SET proxy_path=?,duration=?,state='proxied' WHERE id=?",
                (str(out), media.probe_duration(out), row["id"]))
        else:
            con.execute("UPDATE media SET state='failed',note=? WHERE id=?",
                        (err, row["id"]))
        con.commit()
        print(f"  [{i}/{len(rows)}] {src.name}")

    print("Proxy stage complete.")
