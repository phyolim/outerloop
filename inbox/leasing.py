"""Two layers of mutual exclusion so overlapping cron fires can't double-run work:
(A) a global tick-lock keyed on a heartbeat, and (B) a per-ticket lease.

Staleness is measured from a *heartbeat*, never a fixed wall-clock from start, so a
legitimately long tick is never reaped mid-run (must-fix #1). Liveness authority is
(boot_uuid of a running, freshly-beating tick) + TTL — not os.kill(pid,0), which is
unsound under PID reuse (must-fix #2)."""

import os

from . import config, db

_STALE = f"-{config.LOCK_STALE_SEC} seconds"
_TTL = f"+{config.LEASE_TTL_MIN} minutes"


def acquire_tick_lock(conn, tick_id):
    """Insert this tick's row iff no other tick is running with a fresh heartbeat.
    Returns True if we hold the lock, False if another live tick already does."""
    with db.immediate(conn):
        conn.execute(
            "UPDATE tick_run SET status='crashed', note='stale heartbeat'"
            " WHERE status='running' AND heartbeat_at < datetime('now', ?)",
            (_STALE,),
        )
        live = conn.execute(
            "SELECT COUNT(*) c FROM tick_run"
            " WHERE status='running' AND boot_uuid != ?"
            "   AND heartbeat_at >= datetime('now', ?)",
            (config.BOOT_UUID, _STALE),
        ).fetchone()["c"]
        if live:
            return False
        conn.execute(
            "INSERT INTO tick_run(id, pid, boot_uuid) VALUES(?,?,?)",
            (tick_id, os.getpid(), config.BOOT_UUID),
        )
    return True


def heartbeat(conn, tick_id):
    conn.execute("UPDATE tick_run SET heartbeat_at=datetime('now') WHERE id=?", (tick_id,))


def finish_tick(conn, tick_id, advanced, tokens, note=None):
    conn.execute(
        "UPDATE tick_run SET status='finished', finished_at=datetime('now'),"
        " tickets_advanced=?, tokens=?, note=? WHERE id=?",
        (advanced, tokens, note, tick_id),
    )


def reclaim_expired(conn, tick_id):
    """Delete leases that are past TTL or whose owning tick is no longer live.
    Returns the ticket_ids reclaimed (the worktree reaper consults this)."""
    rows = conn.execute(
        "SELECT ticket_id, owner FROM lease"
        " WHERE expires_at < datetime('now')"
        "    OR owner NOT IN ("
        "        SELECT id FROM tick_run WHERE status='running'"
        "          AND heartbeat_at >= datetime('now', ?))",
        (_STALE,),
    ).fetchall()
    reclaimed = []
    for r in rows:
        with db.immediate(conn):
            conn.execute("DELETE FROM lease WHERE ticket_id=?", (r["ticket_id"],))
            db.append_audit(conn, "recovery", "lease_reclaimed",
                            f"stale lease from tick {r['owner']} reclaimed",
                            ticket_id=r["ticket_id"], tick_id=tick_id)
        reclaimed.append(r["ticket_id"])
    return reclaimed


def reclaim_fleet(conn, tick_id):
    """Coordinator-side reclaim of worker leases. TTL is the authority (a stage
    finishes well within LEASE_TTL_MIN); a reclaimed ticket re-routes and gets a
    fresh epoch on the next claim, so a returning worker's writes are fenced out."""
    rows = conn.execute("SELECT ticket_id, device FROM lease"
                        " WHERE expires_at < datetime('now')").fetchall()
    for r in rows:
        with db.immediate(conn):
            conn.execute("DELETE FROM lease WHERE ticket_id=?", (r["ticket_id"],))
            conn.execute("UPDATE ticket SET assigned_device=NULL WHERE id=?", (r["ticket_id"],))
            db.append_audit(conn, "recovery", "lease_reclaimed",
                            f"TTL-expired lease on ticket {r['ticket_id']} ({r['device']})",
                            ticket_id=r["ticket_id"], tick_id=tick_id)
    return [r["ticket_id"] for r in rows]


def park_stranded(conn, tick_id):
    """Park active, unleased tickets pinned to a device that has been offline (or was
    never seen) for PIN_OFFLINE_PARK_HOURS. A pinned ticket no live device can claim
    would otherwise sit 'active' forever with no attempts accruing and no alarm.
    Parking is reversible from the Parked page once the device returns."""
    rows = conn.execute(
        "SELECT t.id, t.pin FROM ticket t"
        " WHERE t.status='active' AND t.pin IS NOT NULL"
        "   AND t.id NOT IN (SELECT ticket_id FROM lease)"
        "   AND NOT EXISTS (SELECT 1 FROM device d WHERE d.name=t.pin"
        "        AND d.last_seen >= datetime('now', ?))",
        (f"-{config.PIN_OFFLINE_PARK_HOURS} hours",)).fetchall()
    for r in rows:
        with db.immediate(conn):
            conn.execute("UPDATE ticket SET status='parked',"
                         " park_reason='pinned device offline', updated_at=datetime('now')"
                         " WHERE id=?", (r["id"],))
            db.append_audit(conn, "recovery", "parked",
                            f"pinned device '{r['pin']}' unseen for"
                            f" {config.PIN_OFFLINE_PARK_HOURS}h; parked (revive when it returns)",
                            ticket_id=r["id"], tick_id=tick_id, to_stage="parked")
    return [r["id"] for r in rows]


def acquire_lease(conn, ticket_id, tick_id):
    """Atomic claim. Returns True iff this tick now holds the ticket's lease."""
    with db.immediate(conn):
        cur = conn.execute(
            "INSERT INTO lease(ticket_id, owner, pid, boot_uuid, expires_at)"
            " VALUES(?,?,?,?, datetime('now', ?)) ON CONFLICT(ticket_id) DO NOTHING",
            (ticket_id, tick_id, os.getpid(), config.BOOT_UUID, _TTL),
        )
        if cur.rowcount == 1:
            return True
        cur = conn.execute(
            "UPDATE lease SET owner=?, pid=?, boot_uuid=?, acquired_at=datetime('now'),"
            " expires_at=datetime('now', ?) WHERE ticket_id=? AND expires_at < datetime('now')",
            (tick_id, os.getpid(), config.BOOT_UUID, _TTL, ticket_id),
        )
        return cur.rowcount == 1


def release_lease(conn, ticket_id):
    conn.execute("DELETE FROM lease WHERE ticket_id=?", (ticket_id,))
