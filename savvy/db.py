"""SQLite schema and access. One database per work_dir, shared by every stage."""
import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    src_path TEXT UNIQUE,
    event TEXT,
    bytes INTEGER,
    duration REAL,
    proxy_path TEXT,
    state TEXT DEFAULT 'found',
    note TEXT
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    media_id INTEGER,
    start REAL,
    end REAL,
    sharpness REAL,
    brightness REAL,
    motion REAL,
    state TEXT DEFAULT 'candidate',
    score INTEGER,
    tags TEXT,
    shot TEXT,
    note TEXT,
    export_path TEXT,
    my_rating INTEGER,
    my_tags TEXT,
    flagged INTEGER DEFAULT 0,
    reviewed_at TEXT,
    FOREIGN KEY (media_id) REFERENCES media(id)
);
CREATE INDEX IF NOT EXISTS idx_clips_state ON clips(state);
CREATE INDEX IF NOT EXISTS idx_clips_rating ON clips(my_rating);
CREATE INDEX IF NOT EXISTS idx_media_state ON media(state);
"""

# Columns added after v0.1.0 ships. Safe to re-run; failures mean already present.
MIGRATIONS = [
    "ALTER TABLE clips ADD COLUMN my_rating INTEGER",
    "ALTER TABLE clips ADD COLUMN my_tags TEXT",
    "ALTER TABLE clips ADD COLUMN flagged INTEGER DEFAULT 0",
    "ALTER TABLE clips ADD COLUMN reviewed_at TEXT",
]

MEDIA_STATES = ("found", "proxied", "scanned", "needs_reframe", "missing", "failed")
CLIP_STATES = ("candidate", "rejected", "scored", "exported", "error")
REVIEWABLE = ("candidate", "scored", "exported")


def path(cfg) -> Path:
    return config.work_dir(cfg) / "selects.db"


def connect(cfg) -> sqlite3.Connection:
    """Always thread-tolerant: the review server serves requests off worker
    threads. Writers there hold a lock, and every other stage is single-threaded."""
    con = sqlite3.connect(path(cfg), timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for stmt in MIGRATIONS:
        try:
            con.execute(stmt)
        except sqlite3.OperationalError:
            pass
    con.commit()
    return con
