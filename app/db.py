"""Tiny SQLite job store. Traffic is low (one team), so sync sqlite3 with a
short-lived connection per call is plenty and avoids extra dependencies."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    status      TEXT NOT NULL,            -- queued | running | done | failed
    topics      TEXT NOT NULL,            -- JSON list[str]
    context     TEXT DEFAULT '',
    engine      TEXT DEFAULT 'auto',      -- auto | firecrawl | browser
    num_agents  INTEGER DEFAULT 3,
    progress    TEXT DEFAULT '[]',        -- JSON list of event strings
    results     TEXT DEFAULT '[]',        -- JSON list of article dicts
    digest_md   TEXT DEFAULT '',
    error       TEXT DEFAULT ''
);
"""


@contextmanager
def _conn():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.executescript(SCHEMA)


def fail_stale_jobs() -> int:
    """Called on startup: any job still queued/running belonged to a previous
    process (tasks live on the event loop, so a restart strands them)."""
    with _conn() as con:
        cur = con.execute(
            "UPDATE jobs SET status = 'failed', "
            "error = 'Interrupted by server restart — dispatch again.', "
            "updated_at = ? WHERE status IN ('queued', 'running')",
            (time.time(),),
        )
    return cur.rowcount


def create_job(topics: list[str], context: str, engine: str, num_agents: int) -> str:
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _conn() as con:
        con.execute(
            "INSERT INTO jobs (id, created_at, updated_at, status, topics, context, engine, num_agents)"
            " VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)",
            (job_id, now, now, json.dumps(topics), context, engine, num_agents),
        )
    return job_id


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["topics"] = json.loads(d["topics"])
    d["progress"] = json.loads(d["progress"])
    d["results"] = json.loads(d["results"])
    return d


def get_job(job_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_jobs(limit: int = 50) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def set_status(job_id: str, status: str, error: str = ""):
    with _conn() as con:
        con.execute(
            "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, time.time(), job_id),
        )


def log_progress(job_id: str, message: str):
    with _conn() as con:
        row = con.execute("SELECT progress FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return
        events = json.loads(row["progress"])
        events.append(f"{time.strftime('%H:%M:%S')}  {message}")
        con.execute(
            "UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ?",
            (json.dumps(events), time.time(), job_id),
        )


def save_results(job_id: str, results: list[dict], digest_md: str):
    with _conn() as con:
        con.execute(
            "UPDATE jobs SET results = ?, digest_md = ?, updated_at = ? WHERE id = ?",
            (json.dumps(results), digest_md, time.time(), job_id),
        )
