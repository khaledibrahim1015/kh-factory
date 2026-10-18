"""Read-only web UI over the trace database. stdlib only; binds to 127.0.0.1."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..trace import Trace


def serve(root: Path, port: int = 4600) -> None:
    trace = Trace(root)
    jobs_dir = (root / ".factory" / "jobs").resolve()
    page = (resources.files("kh_factory") / "ui" / "index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def json(self, data) -> None:
            self.send(200, json.dumps(data, default=str).encode(), "application/json")

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            parts = [p for p in u.path.split("/") if p]
            if not parts:
                return self.send(200, page, "text/html; charset=utf-8")
            if parts == ["api", "jobs"]:
                return self.json({"root": str(root), "jobs": trace.jobs()})
            if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                job_id = parts[2]
                files = []
                d = jobs_dir / job_id
                if d.is_dir():
                    files = sorted(str(p.relative_to(d)) for p in d.rglob("*")
                                   if p.is_file() and "sessions" not in p.parts)
                return self.json({
                    "job": trace.job(job_id),
                    "phases": trace.query("SELECT * FROM phases WHERE job_id = ? ORDER BY id", (job_id,)),
                    "gates": trace.query("SELECT * FROM gate_results WHERE job_id = ? ORDER BY id", (job_id,)),
                    "files": files,
                })
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "events":
                after = int(q.get("after", 0))
                rows = trace.query("SELECT * FROM events WHERE job_id = ? AND id > ? ORDER BY id LIMIT 500",
                                   (parts[2], after))
                return self.json(rows)
            if parts == ["api", "file"]:
                p = (jobs_dir / q.get("job", "") / q.get("path", "")).resolve()
                if not p.is_relative_to(jobs_dir) or not p.is_file():
                    return self.send(404, b"not found", "text/plain")
                return self.send(200, p.read_bytes()[:2_000_000], "text/plain; charset=utf-8")
            return self.send(404, b"not found", "text/plain")

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"kh-factory UI: http://localhost:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
