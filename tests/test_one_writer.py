"""One durable value, one writer.

Every lasting thing this tool owns - the database tables, and config.json -
should have a known, short list of code allowed to change it. This test reads
the source and fails when that list changes, so a new writer has to be a
deliberate decision rather than something that quietly appeared.

It is a source-text check on purpose: it costs nothing, needs no footage, and
catches the drift at the moment it is introduced.
"""
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "savvy"

# Who is allowed to write each table. Adding a name here should be a decision.
ALLOWED_WRITERS = {
    "library_items": {"library/store.py"},
    "library_choices": {"library/store.py"},
    # media.state has three writers today. That is a real boundary violation,
    # written down in VERIFY.md rather than restructured. Pinning it here means
    # a FOURTH one cannot appear quietly.
    "media": {"stages/proxy.py", "stages/fetch.py", "stages/scan.py"},
    "clips": {"stages/scan.py", "stages/score.py", "stages/export.py",
              "review/server.py"},
}

WRITE_SQL = re.compile(
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)|UPDATE\s+(\w+)\s+SET", re.I)


def _sources():
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def actual_writers():
    """table -> {relative source path} for every INSERT/UPDATE in the package."""
    found = {}
    for p in _sources():
        rel = p.relative_to(PKG).as_posix()
        for insert_tbl, update_tbl in WRITE_SQL.findall(p.read_text()):
            found.setdefault((insert_tbl or update_tbl).lower(), set()).add(rel)
    return found


def test_no_unexpected_table_writers():
    """No writer may appear that isn't on the list.

    Deliberately one-directional: a name on the list that isn't present is
    fine (some stages live in files that aren't committed yet), but a writer
    that isn't on the list fails. What's being guarded is drift, not roll call.
    """
    found = actual_writers()
    assert set(found) <= set(ALLOWED_WRITERS), (
        f"unexpected table written: {sorted(set(found) - set(ALLOWED_WRITERS))}")
    for table, writers in found.items():
        assert writers <= ALLOWED_WRITERS[table], (
            f"{table} is written by {sorted(writers - ALLOWED_WRITERS[table])}, "
            f"which is not on the allowed list {sorted(ALLOWED_WRITERS[table])}. "
            f"A new writer of a durable value needs a deliberate update here.")


def test_the_human_verdict_columns_have_a_single_writer():
    """my_rating / my_tags / flagged are Miles's own calls on his footage.

    Nothing regenerates them, so exactly one file may write them: the review
    server, which is the screen he types them into.
    """
    writers = set()
    for p in _sources():
        rel = p.relative_to(PKG).as_posix()
        for line in p.read_text().splitlines():
            # A write context: either literal UPDATE ... SET, or the column
            # allow-list the review server builds its UPDATE from.
            if re.search(r"UPDATE\s+clips\s+SET", line, re.I) or "sets.append" in line:
                if re.search(r"my_rating|my_tags|flagged", line):
                    writers.add(rel)
        # server.py names the columns one line above the append; catch that too.
        if rel == "review/server.py" and 'for col in ("my_rating"' in p.read_text():
            writers.add(rel)
    assert writers <= {"review/server.py"}, (
        f"human verdict columns also written by "
        f"{sorted(writers - {'review/server.py'})}, expected only "
        f"review/server.py - nothing else may touch Miles's own ratings")


def test_nothing_in_the_package_writes_config_json():
    """config.json is the human's file. The code reads it and never edits it.

    This has been broken once already by a tool editing the file directly;
    the leftover config.json.backup-before-claude is the evidence.
    """
    offenders = []
    for p in _sources():
        text = p.read_text()
        if "config_path" not in text:
            continue
        if re.search(r"config_path\(\)[^\n]*\.write_text|"
                     r"open\(\s*config_path\(\)\s*,\s*['\"][wa]", text):
            offenders.append(p.relative_to(PKG).as_posix())
    assert not offenders, f"these write config.json: {offenders}"


def test_library_values_have_exactly_one_writer():
    found = actual_writers()
    for table in ("library_items", "library_choices"):
        assert found.get(table) == {"library/store.py"}
