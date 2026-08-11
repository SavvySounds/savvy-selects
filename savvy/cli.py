"""Single entry point. `savvy <stage>` or `python3 -m savvy <stage>`."""
import argparse
import shutil
import sys

from . import config, db
from .review import calibrate as _calibrate
from .review import serve
from .stages import export, proxy, scan, score


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


def build_parser():
    ap = argparse.ArgumentParser(prog="savvy", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

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

    r = sub.add_parser("review", help="open the local review deck")
    r.add_argument("--port", type=int, default=8420)
    r.add_argument("--no-open", action="store_true")

    c = sub.add_parser("calibrate", help="report where you and the grader disagree")
    c.add_argument("--min-samples", type=int, default=20)

    sub.add_parser("status", help="counts by stage")
    return ap


COMMANDS = {"proxy": proxy.run, "scan": scan.run, "score": score.run,
            "export": export.run, "review": serve, "calibrate": _calibrate,
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
