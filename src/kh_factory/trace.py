"""SQLite trace (WAL): the source of truth for job state, plus every phase, event, envelope and gate."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = ".factory/trace.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
  branch TEXT, worktree TEXT, base_branch TEXT, base_sha TEXT, reason TEXT DEFAULT '',
  pid INTEGER, created_at REAL, updated_at REAL, heartbeat_at REAL, cost_usd REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS phases (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
  owner TEXT, status TEXT NOT NULL, detail TEXT DEFAULT '', started_at REAL, ended_at REAL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, phase_id INTEGER, ts REAL,
  type TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS envelopes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, phase_id INTEGER, type TEXT, data TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS gate_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, phase_id INTEGER, name TEXT,
  passed INTEGER, detail TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS sessions (
  job_id TEXT NOT NULL, agent TEXT NOT NULL, runner TEXT, session_id TEXT NOT NULL,
  PRIMARY KEY (job_id, agent)
);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id, id);
"""

# Columns added after v1. Old trace.db files get them on open.
MIGRATIONS = {
    "phases": {"context_tokens": "INTEGER DEFAULT 0", "context_window": "INTEGER DEFAULT 0"},
    "jobs": {"max_context_pct": "REAL DEFAULT 0", "code_sha": "TEXT"},
}
CONTEXT_WARN_PCT = 80

# Event types too chatty to store one row per item (token deltas).
SKIP_EVENTS = {"message_update", "stream_event"}

ACTIVE = ("running", "awaiting-human")
STALE_AFTER_S = 120


class Trace:
    def __init__(self, root: Path):
        path = root / DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA)
            for table, cols in MIGRATIONS.items():
                have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
                for col, decl in cols.items():
                    if col not in have:
                        self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.db.execute(sql, args)

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    # ---- jobs ----
    def create_job(self, job_id: str, task_id: str, title: str, **fields) -> None:
        now = time.time()
        self._exec("INSERT INTO jobs (id, task_id, title, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   (job_id, task_id, title, "queued", now, now))
        if fields:
            self.update_job(job_id, **fields)

    def update_job(self, job_id: str, **fields) -> None:
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))

    def job(self, job_id: str) -> dict | None:
        rows = self.query("SELECT * FROM jobs WHERE id = ? OR id LIKE ?", (job_id, f"{job_id}%"))
        return _with_stalled(rows[0]) if len(rows) == 1 else None

    def jobs(self) -> list[dict]:
        return [_with_stalled(r) for r in self.query("SELECT * FROM jobs ORDER BY created_at DESC")]

    def heartbeat(self, job_id: str) -> None:
        self._exec("UPDATE jobs SET heartbeat_at = ?, pid = ? WHERE id = ?", (time.time(), os.getpid(), job_id))

    def add_cost(self, job_id: str, cost: float | None) -> None:
        if cost:
            self._exec("UPDATE jobs SET cost_usd = cost_usd + ? WHERE id = ?", (cost, job_id))

    def set_context(self, job_id: str, phase_id: int, tokens: int, window: int) -> float:
        """Record context use of the latest agent call in a phase. Returns the percent used (0 if unknown)."""
        pct = round(100 * tokens / window, 1) if window else 0.0
        self._exec("UPDATE phases SET context_tokens = ?, context_window = ? WHERE id = ?", (tokens, window, phase_id))
        self._exec("UPDATE jobs SET max_context_pct = MAX(COALESCE(max_context_pct, 0), ?) WHERE id = ?",
                   (pct, job_id))
        return pct

    # ---- phases, events, envelopes, gates ----
    def start_phase(self, job_id: str, name: str, kind: str, owner: str) -> int:
        cur = self._exec("INSERT INTO phases (job_id, name, kind, owner, status, started_at) VALUES (?,?,?,?,?,?)",
                         (job_id, name, kind, owner, "running", time.time()))
        return cur.lastrowid

    def end_phase(self, phase_id: int, status: str, detail: str = "") -> None:
        self._exec("UPDATE phases SET status = ?, detail = ?, ended_at = ? WHERE id = ?",
                   (status, detail, time.time(), phase_id))

    def event(self, job_id: str, phase_id: int | None, ev: dict) -> None:
        if ev.get("type") in SKIP_EVENTS:
            return
        data = json.dumps(_trim(ev), default=str)
        self._exec("INSERT INTO events (job_id, phase_id, ts, type, data) VALUES (?,?,?,?,?)",
                   (job_id, phase_id, time.time(), ev.get("type", "?"), data))

    def envelope(self, job_id: str, phase_id: int, type_: str, data: dict) -> None:
        self._exec("INSERT INTO envelopes (job_id, phase_id, type, data, ts) VALUES (?,?,?,?,?)",
                   (job_id, phase_id, type_, json.dumps(data), time.time()))

    def last_envelope(self, job_id: str, type_: str) -> dict | None:
        rows = self.query("SELECT data FROM envelopes WHERE job_id = ? AND type = ? ORDER BY id DESC LIMIT 1",
                          (job_id, type_))
        return json.loads(rows[0]["data"]) if rows else None

    def gate(self, job_id: str, phase_id: int, name: str, passed: bool, detail: str) -> None:
        self._exec("INSERT INTO gate_results (job_id, phase_id, name, passed, detail, ts) VALUES (?,?,?,?,?,?)",
                   (job_id, phase_id, name, int(passed), detail, time.time()))

    # ---- agent sessions (resume across steps and runs) ----
    def session(self, job_id: str, agent: str) -> str | None:
        rows = self.query("SELECT session_id FROM sessions WHERE job_id = ? AND agent = ?", (job_id, agent))
        return rows[0]["session_id"] if rows else None

    def set_session(self, job_id: str, agent: str, runner: str, session_id: str) -> None:
        self._exec("INSERT OR REPLACE INTO sessions (job_id, agent, runner, session_id) VALUES (?,?,?,?)",
                   (job_id, agent, runner, session_id))

    def clear_session(self, job_id: str, agent: str) -> None:
        self._exec("DELETE FROM sessions WHERE job_id = ? AND agent = ?", (job_id, agent))

    # ---- task status, derived from jobs ----
    def task_status(self, task_id: str) -> str:
        rows = self.query("SELECT * FROM jobs WHERE task_id = ? ORDER BY created_at DESC", (task_id,))
        if any(r["status"] == "approved" for r in rows):
            return "done"
        if not rows:
            return "todo"
        return _with_stalled(rows[0])["status"]


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _with_stalled(row: dict) -> dict:
    """A running job whose process is gone, or silent for too long, is shown as stalled."""
    if row["status"] == "running":
        silent = time.time() - (row.get("heartbeat_at") or row["updated_at"]) > STALE_AFTER_S
        if not _pid_alive(row.get("pid")) or silent:
            row = {**row, "status": "stalled"}
    return row


def _trim(ev: dict, limit: int = 4000) -> dict:
    """Keep events readable in the UI: drop huge system prompts and cut long strings."""
    def cut(v):
        if isinstance(v, str):
            return v if len(v) <= limit else v[:limit] + f"... [{len(v) - limit} chars cut]"
        if isinstance(v, dict):
            return {k: cut(x) for k, x in v.items() if k not in ("sections", "messages")}
        if isinstance(v, list):
            return [cut(x) for x in v[:50]]
        return v
    return cut(ev)
