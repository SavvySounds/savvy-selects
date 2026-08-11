"""Compare your ratings against the grader's and report where they diverge.

Output is meant to be pasted into a chat so the rubric can be rewritten against
real disagreements rather than guesses.
"""


def _mine(row):
    """Map a 0-5 human rating onto the grader's 0-10 scale."""
    return 0 if row["my_rating"] == 0 else row["my_rating"] * 2


def calibrate(cfg, con, args):
    rows = [dict(r) for r in con.execute(
        "SELECT c.score,c.my_rating,c.tags,c.note,c.id,m.event FROM clips c"
        " JOIN media m ON m.id=c.media_id"
        " WHERE c.my_rating IS NOT NULL AND c.score IS NOT NULL")]
    if len(rows) < args.min_samples:
        raise SystemExit(f"Only {len(rows)} rated clips. "
                         f"Review at least {args.min_samples} first.")

    diffs = [(_mine(r) - r["score"], r) for r in rows]
    avg = sum(d for d, _ in diffs) / len(diffs)

    print(f"\n{len(rows)} clips rated by both you and the grader.")
    print(f"Average gap (yours minus grader): {avg:+.2f}")
    if avg > 1:
        print("  The grader is running harsh. Loosen what the rubric rewards.")
    elif avg < -1:
        print("  The grader is running generous. Tighten the rubric.")
    else:
        print("  Well calibrated overall.")

    print("\nGRADER LIKED, YOU DIDN'T  (stop rewarding these)")
    for d, r in sorted(diffs, key=lambda x: x[0])[:8]:
        print(f"  gap {d:+3d}  [{(r['event'] or '')[:22]:<22}] "
              f"{r['tags'] or ''} - {r['note'] or ''}")

    print("\nYOU LIKED, GRADER MISSED  (add these signals)")
    for d, r in sorted(diffs, key=lambda x: -x[0])[:8]:
        print(f"  gap {d:+3d}  [{(r['event'] or '')[:22]:<22}] "
              f"{r['tags'] or ''} - {r['note'] or ''}")

    by_tag = {}
    for d, r in diffs:
        for t in (r["tags"] or "").split(","):
            if t.strip():
                by_tag.setdefault(t.strip(), []).append(d)
    ranked = sorted(((sum(v) / len(v), t, len(v))
                     for t, v in by_tag.items() if len(v) >= 3), key=lambda x: x[0])
    if ranked:
        print("\nBY TAG (negative = grader overrates it)")
        for g, t, n in ranked[:6] + ranked[-6:][::-1]:
            print(f"  {g:+5.1f}  {t:<24} n={n}")

    print("\nPaste this into chat and the rubric gets rewritten to match your ear.\n")
