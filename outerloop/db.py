"""SQLite connection factory + the two primitives every module relies on:
an append_audit() helper and a BEGIN IMMEDIATE transaction context manager."""

import json
import re
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


def _pre_migrate(conn):
    """One-time rename of the pre-'worker' fleet schema (table + columns were 'device').
    MUST run before the schema script, so its `CREATE TABLE IF NOT EXISTS worker` doesn't
    create an empty table beside the old data. No-op on fresh DBs."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "device" in tables and "worker" not in tables:
        conn.execute("ALTER TABLE device RENAME TO worker")
    for table, old, new in (("ticket", "assigned_device", "assigned_worker"),
                            ("lease", "device", "worker"),
                            ("request_log", "device", "worker")):
        if table in tables:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if old in cols and new not in cols:
                conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")


def _migrate(conn):
    """Idempotently add columns to DBs created before a schema change. CREATE TABLE
    IF NOT EXISTS covers fresh DBs and new tables; this covers new columns."""
    _ensure_columns(conn, "ticket", {
        "requires": "requires TEXT NOT NULL DEFAULT '[]'", "prefer": "prefer TEXT",
        "pin": "pin TEXT", "assigned_worker": "assigned_worker TEXT",
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
        "epoch": "epoch INTEGER NOT NULL DEFAULT 0", "worker": "worker TEXT",
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
    _rebuild_agent_run(conn)


def _rebuild_agent_run(conn):
    """The 'shipper' role (v0.3.7 lifecycle) is missing from agent_run's role CHECK on
    DBs created earlier, so every shipper run 500s on INSERT and its opening_pr stage
    spins until the stall guard fails the ticket. SQLite can't ALTER a CHECK, so those
    DBs get a one-time rebuild — in ONE transaction, so a crash mid-rebuild can't
    leave a fresh empty agent_run beside a stranded copy of the history."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table'"
                       " AND name='agent_run'").fetchone()
    if not row or "'shipper'" in row["sql"]:
        return
    # The new DDL comes from schema.sql itself (no duplicated CREATE to drift), pulled
    # out as a single statement because executescript would commit mid-transaction.
    m = re.search(r"CREATE TABLE IF NOT EXISTS agent_run\s*\(.*?\n\);",
                  config.SCHEMA_PATH.read_text(), re.S)
    assert m, "agent_run CREATE not found in schema.sql"
    with immediate(conn):
        conn.execute("ALTER TABLE agent_run RENAME TO agent_run_pre_shipper")
        conn.execute(m.group(0))
        # Copy by explicit shared-column list: an old DB's column ORDER differs from a
        # fresh CREATE when columns arrived via earlier _ensure_columns migrations.
        new = [r["name"] for r in conn.execute("PRAGMA table_info(agent_run)")]
        old = {r["name"] for r in conn.execute("PRAGMA table_info(agent_run_pre_shipper)")}
        cols = ",".join(c for c in new if c in old)
        conn.execute(f"INSERT INTO agent_run({cols})"
                     f" SELECT {cols} FROM agent_run_pre_shipper")
        conn.execute("DROP TABLE agent_run_pre_shipper")  # takes the renamed index with it
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_run_ticket ON agent_run(ticket_id)")


def init_db():
    conn = connect()
    _pre_migrate(conn)
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


def log_request(conn, worker, method, path, status):
    """Best-effort raw request log, self-pruned to the newest REQUEST_LOG_CAP rows."""
    conn.execute("INSERT INTO request_log(worker, method, path, status) VALUES(?,?,?,?)",
                 (worker, method, path, status))
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
