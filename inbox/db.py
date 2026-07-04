"""SQLite connection factory + the two primitives every module relies on:
an append_audit() helper and a BEGIN IMMEDIATE transaction context manager."""

import json
import sqlite3
from contextlib import contextmanager

from . import config


def connect():
    """Autocommit connection (isolation_level=None) so we drive BEGIN IMMEDIATE
    ourselves. WAL = concurrent readers + one writer (UI reads while tick writes)."""
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_columns(conn, table, cols):
    have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in cols.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _migrate(conn):
    """Idempotently add columns to DBs created before a schema change. CREATE TABLE
    IF NOT EXISTS covers fresh DBs and new tables; this covers new columns."""
    _ensure_columns(conn, "ticket", {
        "requires": "requires TEXT NOT NULL DEFAULT '[]'", "prefer": "prefer TEXT",
        "pin": "pin TEXT", "assigned_device": "assigned_device TEXT",
        "claim_epoch": "claim_epoch INTEGER NOT NULL DEFAULT 0", "dedup_key": "dedup_key TEXT",
        "project": "project TEXT",
        "kind": "kind TEXT CHECK(kind IN ('feature','bug','chore','research','ops'))",
        "draft": "draft INTEGER NOT NULL DEFAULT 0",
    })
    # Backfill the user-facing `kind` for rows created before it existed, derived from
    # the coarse routing `type` (coding->feature, knowledge->research, ops->ops).
    conn.execute(
        "UPDATE ticket SET kind = CASE type WHEN 'coding' THEN 'feature'"
        " WHEN 'knowledge' THEN 'research' WHEN 'ops' THEN 'ops' ELSE 'feature' END"
        " WHERE kind IS NULL")
    _ensure_columns(conn, "lease", {
        "epoch": "epoch INTEGER NOT NULL DEFAULT 0", "device": "device TEXT",
        # claim-time snapshot so the hub computes stall-progress itself (must-fix #3)
        "claim_sub_stage": "claim_sub_stage TEXT", "claim_status": "claim_status TEXT",
    })
    # Token accounting (USD is legacy): model used + tokens consumed per run.
    _ensure_columns(conn, "agent_run", {
        "model": "model TEXT",
        "tokens_in": "tokens_in INTEGER NOT NULL DEFAULT 0",
        "tokens_out": "tokens_out INTEGER NOT NULL DEFAULT 0",
    })
    _ensure_columns(conn, "tick_run", {"tokens": "tokens INTEGER NOT NULL DEFAULT 0"})
    # 'rework' rides on status='rejected' because the CHECK constraint on older DBs
    # can't be altered without a table rebuild.
    _ensure_columns(conn, "decision", {"rework": "rework INTEGER NOT NULL DEFAULT 0"})


def init_db():
    conn = connect()
    conn.executescript(config.SCHEMA_PATH.read_text())
    _migrate(conn)
    for k, v in config.SETTINGS_DEFAULTS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
    return conn


@contextmanager
def immediate(conn):
    """A writer-locked, atomic transaction. Take the write lock up front so a
    multi-step state transition can't interleave with another writer."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def append_audit(conn, actor, action, reason, *, ticket_id=None, tick_id=None,
                 from_stage=None, to_stage=None, detail=None):
    conn.execute(
        "INSERT INTO audit(ticket_id, tick_id, actor, action, from_stage, to_stage, reason, detail)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (ticket_id, tick_id, actor, action, from_stage, to_stage, reason,
         json.dumps(detail or {})),
    )


REQUEST_LOG_CAP = 500  # rolling window of raw API requests kept for debugging


def log_request(conn, device, method, path, status):
    """Best-effort raw request log, self-pruned to the newest REQUEST_LOG_CAP rows."""
    conn.execute("INSERT INTO request_log(device, method, path, status) VALUES(?,?,?,?)",
                 (device, method, path, status))
    conn.execute("DELETE FROM request_log WHERE id <= (SELECT MAX(id) - ? FROM request_log)",
                 (REQUEST_LOG_CAP,))


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def hstate(ticket):
    """Decode a ticket row's handler_state JSON."""
    return json.loads(ticket["handler_state"] or "{}")
