"""Server-arbitrated ticket claiming + release. Runs on the hub only.

Claiming NEVER takes over a lease in-place; the scheduler's reclaim_expired DELETEs
stale leases, and a claim only ever INSERTs for an unleased ticket — bumping the
ticket's monotonic claim_epoch as it does (must-fix #1). Routing is decided from the
authenticated worker row's capabilities, never the request body (must-fix #5).
Release computes stall-progress from the claim-time snapshot, on the hub, not from
anything the worker reports (must-fix #3)."""

import json

from . import config, db

_TTL = f"+{config.LEASE_TTL_MIN} minutes"


def window_spend(conn):
    """Total agent tokens over the rolling window — shared across all workers."""
    return conn.execute(
        "SELECT COALESCE(SUM(tokens_in + tokens_out),0) s FROM agent_run"
        " WHERE created_at > datetime('now', ?)",
        (f"-{config.FLEET_SPEND_WINDOW_HOURS} hours",)).fetchone()["s"]


def over_budget(conn):
    cap = int(db.get_setting(conn, "fleet_budget_tokens", config.FLEET_BUDGET_TOKENS))
    return window_spend(conn) >= cap


def _caps_ok(requires, caps):
    """Every required tag must be satisfied. 'foo:*' matches any 'foo:...' capability."""
    for r in requires:
        if r.endswith(":*"):
            prefix = r[:-1]  # 'repos:*' -> 'repos:'
            if not any(c == r or c.startswith(prefix) for c in caps):
                return False
        elif r not in caps:
            return False
    return True


def claim(conn, worker_name):
    """Atomically lease the highest-priority ready ticket this worker may run.
    Returns {'ticket': {...}, 'epoch': n} or None."""
    with db.immediate(conn):
        if db.get_setting(conn, "kill_switch", "off") == "on":
            return None
        if over_budget(conn):  # hub-wide spend ceiling (#2) — no worker shares a tick
            return None
        dev = conn.execute("SELECT * FROM worker WHERE name=?", (worker_name,)).fetchone()
        if not dev or dev["status"] != "online":   # paused/draining => don't claim
            return None
        caps = set(json.loads(dev["capabilities"] or "[]"))
        rows = conn.execute(
            "SELECT * FROM ticket WHERE status='active'"
            "   AND id NOT IN (SELECT ticket_id FROM lease)"
            # must-fix #4: never claim a ticket whose answered decision the hub hasn't resumed yet
            "   AND (blocked_by_decision_id IS NULL"
            "        OR blocked_by_decision_id IN (SELECT id FROM decision WHERE consumed=1))"
            " ORDER BY (prefer IS NOT NULL AND prefer=?) DESC,"
            "          score IS NULL, score DESC, created_at ASC",
            (worker_name,)).fetchall()
        for t in rows:
            if t["pin"] and t["pin"] != worker_name:
                continue
            if not _caps_ok(json.loads(t["requires"] or "[]"), caps):
                continue
            epoch = (t["claim_epoch"] or 0) + 1
            conn.execute("UPDATE ticket SET claim_epoch=?, assigned_worker=? WHERE id=?",
                         (epoch, worker_name, t["id"]))
            conn.execute(
                "INSERT INTO lease(ticket_id, owner, pid, boot_uuid, worker, epoch,"
                " claim_sub_stage, claim_status, expires_at)"
                " VALUES(?,?,?,?,?,?,?,?, datetime('now', ?))",
                (t["id"], worker_name, 0, worker_name, worker_name, epoch,
                 t["sub_stage"], t["status"], _TTL))
            db.append_audit(conn, "claim", "claimed",
                            f"{worker_name} claimed ticket {t['id']} (epoch {epoch})",
                            ticket_id=t["id"], detail={"worker": worker_name, "epoch": epoch})
            out = {k: t[k] for k in t.keys()}
            out["claim_epoch"], out["assigned_worker"] = epoch, worker_name
            return {"ticket": out, "epoch": epoch}
        return None


def release(conn, ticket_id, epoch):
    """Worker finished a stage. Fenced by epoch. Computes progress from the lease's
    claim-time snapshot and updates the stall counter, then drops the lease."""
    with db.immediate(conn):
        lease = conn.execute("SELECT * FROM lease WHERE ticket_id=?", (ticket_id,)).fetchone()
        if not lease or lease["epoch"] != epoch:
            return {"stale": True}  # already reclaimed by the scheduler; ignore
        t = conn.execute("SELECT * FROM ticket WHERE id=?", (ticket_id,)).fetchone()
        progressed = (t["status"] in ("done", "failed", "blocked")
                      or t["sub_stage"] != lease["claim_sub_stage"]
                      or t["status"] != lease["claim_status"])
        if progressed:
            if t["attempts"]:
                conn.execute("UPDATE ticket SET attempts=0 WHERE id=?", (ticket_id,))
        else:
            n = t["attempts"] + 1
            if n > config.MAX_ATTEMPTS:
                conn.execute("UPDATE ticket SET status='failed', updated_at=datetime('now')"
                             " WHERE id=?", (ticket_id,))
                db.append_audit(conn, "recovery", "failed",
                                f"no progress after {n} claims on '{t['sub_stage']}'",
                                ticket_id=ticket_id)
            else:
                conn.execute("UPDATE ticket SET attempts=? WHERE id=?", (n, ticket_id))
        conn.execute("UPDATE ticket SET assigned_worker=NULL WHERE id=?", (ticket_id,))
        conn.execute("DELETE FROM lease WHERE ticket_id=?", (ticket_id,))
    return {"ok": True}


def renew(conn, ticket_id, epoch):
    """Extend the lease iff the caller still holds it at the right epoch. A worker
    calls this immediately before an irreversible effect (merge); a None return means
    the lease was reclaimed and the worker must abort BEFORE acting (must-fix #6)."""
    with db.immediate(conn):
        lease = conn.execute("SELECT epoch FROM lease WHERE ticket_id=?", (ticket_id,)).fetchone()
        if not lease or lease["epoch"] != epoch:
            return None
        conn.execute("UPDATE lease SET expires_at=datetime('now', ?) WHERE ticket_id=?",
                     (_TTL, ticket_id))
    return {"ok": True}
