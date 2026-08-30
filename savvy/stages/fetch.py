"""Bring parked cloud files down to disk, deliberately and within a budget.

`proxy` refuses to touch online-only files because opening one starts a
download. This is the opt-in: pick an event, check it fits, pull it down. After
this, run `proxy` again and the files get picked up normally.
"""
from pathlib import Path

from .. import config, storage

# Never fill the disk to the brim. Proxies, exports and macOS all need room.
SAFETY_MARGIN = 5 * storage.GB


def run(cfg, con, args):
    rows = con.execute("SELECT * FROM media WHERE state='cloud_only'"
                       " ORDER BY event, src_path").fetchall()
    if args.event:
        want = args.event.strip().lower()
        rows = [r for r in rows if r["event"].lower() == want]
        if not rows:
            events = {r["event"] for r in con.execute(
                "SELECT event FROM media WHERE state='cloud_only'")}
            print(f"Nothing parked under {args.event!r}.")
            if events:
                print("Parked events:")
                for e in sorted(events):
                    print(f"  {e}")
            return

    if not rows:
        print("Nothing parked. Everything in your sources is already on disk.")
        return

    budget = int(args.max_gb * storage.GB) if args.max_gb else None
    picked, total = [], 0
    for r in rows:
        size = r["bytes"] or 0
        if budget is not None and total + size > budget:
            continue
        picked.append(r)
        total += size

    skipped = len(rows) - len(picked)
    print(f"{len(rows)} files parked, {storage.human(sum(r['bytes'] or 0 for r in rows))}.")
    if skipped:
        print(f"  {len(picked)} fit your --max-gb budget, {skipped} left parked.")

    if not picked:
        print("Budget too small for even one file. Raise --max-gb.")
        return

    free = storage.free_bytes(config.work_dir(cfg))
    print(f"Downloading {storage.human(total)}. Free space {storage.human(free)}.")

    if total > free - SAFETY_MARGIN:
        raise SystemExit(
            f"Not enough room. {storage.human(total)} needed, "
            f"{storage.human(max(free - SAFETY_MARGIN, 0))} usable "
            f"(keeping {storage.human(SAFETY_MARGIN)} spare).\n"
            f"Fetch one event at a time, or use --max-gb.")

    if args.dry_run:
        print("\nDry run. Nothing downloaded. These would come down:")
        for r in picked:
            print(f"  {storage.human(r['bytes'] or 0):>9}  {r['event']}/"
                  f"{Path(r['src_path']).name}")
        return

    done = failed = 0
    for i, r in enumerate(picked, 1):
        src = Path(r["src_path"])
        if not src.exists():
            con.execute("UPDATE media SET state='missing' WHERE id=?", (r["id"],))
            con.commit()
            continue
        try:
            storage.materialize(src)
        except OSError as e:
            con.execute("UPDATE media SET note=? WHERE id=?", (str(e), r["id"]))
            con.commit()
            failed += 1
            print(f"  [{i}/{len(picked)}] FAILED {src.name}: {e}")
            continue

        # Confirm it actually landed rather than trusting the read.
        if storage.is_cloud_only_path(src):
            failed += 1
            print(f"  [{i}/{len(picked)}] still online-only: {src.name}")
            continue

        con.execute("UPDATE media SET state='found', note=NULL WHERE id=?", (r["id"],))
        con.commit()
        done += 1
        print(f"  [{i}/{len(picked)}] {src.name}")

    print(f"\nFetched {done} files"
          + (f", {failed} failed" if failed else "")
          + ". Now run: savvy proxy")
