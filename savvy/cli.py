"""Single entry point. `savvy <stage>` or `python3 -m savvy <stage>`."""
import argparse
import shutil
import sys

from . import config, db, storage
from .review import calibrate as _calibrate
from .review import serve
from .stages import assemble, export, fetch, proxy, scan, score


def status(cfg, con, args):
    print(f"work dir: {cfg['work_dir']}\n\nMEDIA")
    for r in con.execute("SELECT state, COUNT(*) n, SUM(bytes)/1e9 gb"
                         " FROM media GROUP BY state ORDER BY n DESC"):
        print(f"  {r['state']:<16} {r['n']:>6}   {r['gb'] or 0:.1f} GB")
    print("\nCLIPS")
    for r in con.execute("SELECT state, COUNT(*) n FROM clips"
                         " GROUP BY state ORDER BY n DESC"):
        print(f"  {r['state']:<16} {r['n']:>6}")
    print()
    for label, sql in [("grader 7+", "score >= 7"),
                       ("you rated", "my_rating IS NOT NULL"),
                       ("reel crate", "flagged = 1")]:
        n = con.execute(f"SELECT COUNT(*) n FROM clips WHERE {sql}").fetchone()["n"]
        print(f"  {label:<14} {n:>6}")
    n = con.execute("SELECT COUNT(*) n FROM media"
                    " WHERE state='needs_reframe'").fetchone()["n"]
    if n:
        print(f"\n  {n} Insta360 files parked. Reframe to flat video, re-run proxy.")

    r = con.execute("SELECT COUNT(*) n, SUM(bytes) b FROM media"
                    " WHERE state='cloud_only'").fetchone()
    if r["n"]:
        print(f"\n  {r['n']} files online-only ({storage.human(r['b'] or 0)}), "
              f"not on this disk.\n  savvy fetch --event \"NAME\" to bring one down.")


def check(cfg, con, args):
    """Read-only: what is actually on this disk versus still in the cloud.

    Never opens a file, so running this cannot trigger a download.
    """
    events = storage.survey(cfg["sources"], cfg["video_ext"] + cfg["insta360_ext"])
    if not events:
        print("No video found in your sources. Check the paths in config.json.")
        return

    print(f"{'event':<38} {'on disk':>10} {'in cloud':>10}  files")
    for e in events:
        n = e["local_files"] + e["cloud_files"]
        print(f"{e['event'][:37]:<38} {storage.human(e['local_bytes']):>10}"
              f" {storage.human(e['cloud_bytes']):>10}"
              f"  {e['local_files']}/{n} local")

    loc = sum(e["local_bytes"] for e in events)
    cld = sum(e["cloud_bytes"] for e in events)
    nloc = sum(e["local_files"] for e in events)
    ncld = sum(e["cloud_files"] for e in events)
    free = storage.free_bytes(config.work_dir(cfg))

    print(f"\n{nloc} files on disk ({storage.human(loc)})")
    print(f"{ncld} files online-only ({storage.human(cld)})")
    print(f"{storage.human(free)} free on the drive holding {cfg['work_dir']}")
    if cld > free:
        print(f"\nPulling all of it down would need {storage.human(cld)} and you "
              f"have {storage.human(free)}.\nFetch one event at a time:"
              f"\n  savvy fetch --event \"NAME\"")


def build_parser():
    ap = argparse.ArgumentParser(prog="savvy", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="what is on disk vs still in the cloud")

    f = sub.add_parser("fetch", help="download parked online-only files")
    f.add_argument("--event", help="only this event folder")
    f.add_argument("--max-gb", type=float, default=0.0,
                   help="stop after roughly this many GB")
    f.add_argument("--dry-run", action="store_true",
                   help="list what would come down, download nothing")

    sub.add_parser("proxy", help="index sources and build 720p proxies")
    sub.add_parser("scan", help="split into shots, reject unusable ones locally")

    s = sub.add_parser("score", help="grade shots with Claude vision (paid)")
    s.add_argument("--limit", type=int, default=0)

    e = sub.add_parser("export", help="cut winners from the originals")
    e.add_argument("--min-score", type=int, default=7)
    e.add_argument("--use-ratings", action="store_true",
                   help="use your review ratings instead of the grader's score")
    e.add_argument("--min-rating", type=int, default=4)
    e.add_argument("--crate", action="store_true", help="only the reel crate")
    e.add_argument("--limit", type=int, default=0)

    a = sub.add_parser("assemble", help="beat-synced rough cut EDL from the reel crate")
    a.add_argument("--track", required=True, help="path to the DJ track to cut to")
    a.add_argument("--bpm", type=float, help="override detected BPM")
    a.add_argument("--offset", type=float, help="override detected first-beat time, seconds")
    a.add_argument("--bars", type=int, default=2, help="clip length in bars (4 beats/bar)")
    a.add_argument("--fps", type=float, default=29.97, help="EDL sequence frame rate")
    a.add_argument("--out", help="output .edl path (default: work_dir/SELECTS/)")

    r = sub.add_parser("review", help="open the local review deck")
    r.add_argument("--port", type=int, default=8420)
    r.add_argument("--no-open", action="store_true")

    c = sub.add_parser("calibrate", help="report where you and the grader disagree")
    c.add_argument("--min-samples", type=int, default=20)

    sub.add_parser("status", help="counts by stage")
    return ap


COMMANDS = {"check": check, "fetch": fetch.run, "proxy": proxy.run,
            "scan": scan.run, "score": score.run, "export": export.run,
            "assemble": assemble.run, "review": serve, "calibrate": _calibrate,
            "status": status}


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found. brew install ffmpeg")
    cfg = config.load()
    con = db.connect(cfg)
    COMMANDS[args.cmd](cfg, con, args)


if __name__ == "__main__":
    main()
