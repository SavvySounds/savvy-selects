"""Local review deck. Serves index.html plus a small JSON + media API.

Byte-range support is not optional: without it the browser cannot seek video,
which makes the filmstrip scrubber useless.
"""
import json
import mimetypes
import re
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .. import config, db, media

HTML = Path(__file__).parent / "index.html"

SORTS = {"score": "c.score DESC, c.sharpness DESC",
         "event": "m.event, c.start",
         "sharp": "c.sharpness DESC"}


def make_handler(cfg, con, lock, strips):
    def query(sql, params=()):
        with lock:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def write(sql, params=()):
        with lock:
            con.execute(sql, params)
            con.commit()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json", code=200):
            if isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path, head_only=False):
            if not path or not Path(path).exists():
                return self._send(b"", "text/plain", 404)
            path = Path(path)
            size = path.stat().st_size
            ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            rng = self.headers.get("Range")

            if head_only or not rng:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                if not head_only:
                    with open(path, "rb") as f:
                        self.wfile.write(f.read())
                return

            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            start = int(m.group(1) or 0)
            end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def _resolve(self, path):
            if path.startswith("/media/"):
                rows = query("SELECT proxy_path FROM media WHERE id=?",
                             (path.split("/")[-1],))
                return rows[0]["proxy_path"] if rows else None
            if path.startswith("/strip/"):
                cid = path.split("/")[-1].replace(".jpg", "")
                cached = strips / f"{cid}.jpg"
                if not cached.exists():
                    rows = query("SELECT c.start,c.end,m.proxy_path FROM clips c"
                                 " JOIN media m ON m.id=c.media_id WHERE c.id=?", (cid,))
                    if rows and rows[0]["proxy_path"]:
                        media.filmstrip(cached, rows[0]["proxy_path"],
                                        rows[0]["start"], rows[0]["end"])
                return cached if cached.exists() else None
            return None

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self._send(HTML.read_text(), "text/html; charset=utf-8")
            if u.path == "/api/clips":
                return self._send(json.dumps(self._clips(parse_qs(u.query))))
            if u.path.startswith(("/media/", "/strip/")):
                return self._file(self._resolve(u.path))
            return self._send(b"", "text/plain", 404)

        def do_HEAD(self):
            u = urlparse(self.path)
            if u.path.startswith(("/media/", "/strip/")):
                return self._file(self._resolve(u.path), head_only=True)
            return self._send(b"", "text/plain", 404)

        def do_POST(self):
            if urlparse(self.path).path != "/api/rate":
                return self._send(b"", "text/plain", 404)
            n = int(self.headers.get("Content-Length", 0))
            d = json.loads(self.rfile.read(n) or "{}")
            sets, params = [], []
            for col in ("my_rating", "my_tags", "flagged"):
                if col in d:
                    sets.append(f"{col}=?")
                    params.append(d[col])
            if sets:
                sets.append("reviewed_at=?")
                params += [datetime.now().isoformat(timespec="seconds"), d.get("id")]
                write(f"UPDATE clips SET {', '.join(sets)} WHERE id=?", params)
            return self._send(json.dumps({"ok": True}))

        def _clips(self, q):
            event = (q.get("event") or [""])[0]
            state = (q.get("state") or ["todo"])[0]
            sort = (q.get("sort") or ["score"])[0]
            where = ["c.state IN ('candidate','scored','exported')"]
            params = []
            if event:
                where.append("m.event = ?")
                params.append(event)
            if state == "todo":
                where.append("c.my_rating IS NULL")
            elif state == "rated":
                where.append("c.my_rating IS NOT NULL")
            elif state == "flagged":
                where.append("c.flagged = 1")

            clips = query(
                f"SELECT c.*, m.event, m.src_path FROM clips c JOIN media m ON m.id=c.media_id"
                f" WHERE {' AND '.join(where)} ORDER BY {SORTS.get(sort, SORTS['score'])}"
                f" LIMIT 400", params)
            for c in clips:
                c["filename"] = Path(c["src_path"]).name
            return {
                "clips": clips,
                "total": query("SELECT COUNT(*) n FROM clips WHERE state IN"
                               " ('candidate','scored','exported')")[0]["n"],
                "reviewed": query("SELECT COUNT(*) n FROM clips"
                                  " WHERE my_rating IS NOT NULL")[0]["n"],
                "events": [r["event"] for r in query(
                    "SELECT DISTINCT event FROM media WHERE event IS NOT NULL"
                    " ORDER BY event")],
            }

    return Handler


def serve(cfg, con, args):
    strips = config.work_dir(cfg) / "filmstrips"
    strips.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    n = con.execute("SELECT COUNT(*) n FROM clips WHERE state IN"
                    " ('candidate','scored','exported')").fetchone()["n"]
    if not n:
        raise SystemExit("No clips yet. Run `savvy scan` first.")

    url = f"http://localhost:{args.port}"
    print(f"Savvy Review - {n} clips loaded\n{url}\n\n"
          "  1-5 rate    X cut    F reel crate    T tag\n"
          "  arrows move    space hold    ctrl-c quit\n")
    if not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(cfg, con, lock, strips))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        rated = con.execute("SELECT COUNT(*) n FROM clips"
                            " WHERE my_rating IS NOT NULL").fetchone()["n"]
        print(f"\nStopped. {rated} clips rated so far.")
