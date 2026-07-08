"""The cron entrypoint. One tick advances each selected ticket by AT MOST one bounded
stage and persists only committed transitions, so re-running a tick (or recovering
from a crash) is safe by construction."""

import json
import threading
import uuid

from . import config, db, gate, git_ops, leasing, scoring, triage
from .context import Ctx, StaleEpoch
from .handlers import base as hbase
from .handlers import get_handler


def _kill_engaged(conn):
    return db.get_setting(conn, "kill_switch", "off") == "on" or config.KILL_FILE.exists()


def _resume(ctx, t, dec, handler):
    """Apply a human-answered decision: approve -> move to resume_stage; rework ->
    thread the note back and hand to on_rework; reject -> hand to the handler.
    Consumes the decision so it can't fire twice."""
    conn = ctx.conn
    if dec["status"] == "approved":
        with db.immediate(conn):
            if dec["kind"] == "clarification":
                # Thread the human's answer back into handler_state so the author sees it
                # on the resumed run (resume_stage is 'groomed', which re-invokes the author).
                hs = db.hstate(t)
                hs.setdefault("clarifications", []).append(
                    {"q": dec["question"], "a": dec["answer_note"] or ""})
                conn.execute("UPDATE ticket SET handler_state=? WHERE id=?",
                             (json.dumps(hs), t["id"]))
            conn.execute(
                "UPDATE ticket SET status='active', sub_stage=?, blocked_by_decision_id=NULL,"
                " updated_at=datetime('now') WHERE id=?", (dec["resume_stage"], t["id"]))
            gate.consume(conn, dec["id"])
            db.append_audit(conn, "human", "decision_approved",
                            f"approved {dec['kind']} -> {dec['resume_stage']}",
                            ticket_id=t["id"], tick_id=ctx.tick_id,
                            to_stage=dec["resume_stage"], detail={"decision_id": dec["id"]})
        return f"approved {dec['kind']}"
    if dec["rework"]:
        # Not a go/no-go: the human sent feedback. Thread it through the operator-note
        # channel (survives worker hs overwrites — see context._keep_operator_notes)
        # and let the handler route the ticket back to a work stage.
        with db.immediate(conn):
            # Re-read INSIDE the write txn: a web-thread operator note committed after
            # `t` was fetched would be clobbered by the stale handler_state snapshot.
            hs = db.hstate(conn.execute("SELECT * FROM ticket WHERE id=?", (t["id"],)).fetchone())
            if dec["answer_note"]:
                hs.setdefault("clarifications", []).append(
                    {"q": "(operator note)", "a": dec["answer_note"]})
            conn.execute("UPDATE ticket SET status='active', handler_state=?,"
                         " blocked_by_decision_id=NULL, updated_at=datetime('now') WHERE id=?",
                         (json.dumps(hs), t["id"]))
            gate.consume(conn, dec["id"])
            db.append_audit(conn, "human", "decision_rework",
                            f"requested changes on {dec['kind']}", ticket_id=t["id"],
                            tick_id=ctx.tick_id, detail={"decision_id": dec["id"]})
        fresh = conn.execute("SELECT * FROM ticket WHERE id=?", (t["id"],)).fetchone()
        handler.on_rework(ctx, fresh, dec)
        return f"rework {dec['kind']}"
    with db.immediate(conn):
        conn.execute("UPDATE ticket SET status='active', blocked_by_decision_id=NULL WHERE id=?",
                     (t["id"],))
        gate.consume(conn, dec["id"])
        db.append_audit(conn, "human", "decision_rejected", f"rejected {dec['kind']}",
                        ticket_id=t["id"], tick_id=ctx.tick_id, detail={"decision_id": dec["id"]})
    fresh = conn.execute("SELECT * FROM ticket WHERE id=?", (t["id"],)).fetchone()
    handler.on_reject(ctx, fresh, dec)
    return f"rejected {dec['kind']}"


def _ticket_tokens(conn, ticket_id):
    return conn.execute(
        "SELECT COALESCE(SUM(tokens_in + tokens_out),0) s FROM agent_run WHERE ticket_id=?",
        (ticket_id,)).fetchone()["s"]


def _tick_tokens(conn, tick_id):
    return conn.execute(
        "SELECT COALESCE(SUM(tokens_in + tokens_out),0) s FROM agent_run WHERE tick_id=?",
        (tick_id,)).fetchone()["s"]


def _process_one(ctx, t, handler):
    """Advance one ticket by one stage (or resume its answered decision). Enforces the
    cumulative-cost ceiling up front; the stall ceiling is handled by _account_attempt."""
    conn = ctx.conn
    tk = conn.execute("SELECT * FROM ticket WHERE id=?", (t["id"],)).fetchone()
    if _ticket_tokens(conn, tk["id"]) > ctx.cfg.TICKET_BUDGET_TOKENS:
        hbase.fail(ctx, tk,
                   f"exceeded {ctx.cfg.TICKET_BUDGET_TOKENS:,}-token cumulative budget")
        return "failed: ticket budget"
    dec = gate.answered_decision(conn, tk)
    if dec:
        return _resume(ctx, tk, dec, handler)
    return handler.advance(ctx, tk)


def _account_attempt(ctx, after, progressed):
    """A ticket that changes state (incl. going blocked/done/failed) made progress and
    resets its stall counter. A tick that left the ticket untouched (a stage that errors
    or no-ops every time) climbs toward MAX_ATTEMPTS, then the ticket is failed (#6)."""
    conn = ctx.conn
    if progressed:
        if after["attempts"]:
            conn.execute("UPDATE ticket SET attempts=0 WHERE id=?", (after["id"],))
        return
    n = after["attempts"] + 1
    if n > ctx.cfg.MAX_ATTEMPTS:
        hbase.fail(ctx, after, f"no progress after {n} ticks on stage '{after['sub_stage']}'")
    else:
        conn.execute("UPDATE ticket SET attempts=? WHERE id=?", (n, after["id"]))


def _backup(conn):
    """One rotating VACUUM INTO snapshot (must-have nice-to-have): cheap insurance
    against a WAL-checkpoint corruption losing the single hub."""
    snap = config.BACKUPS_DIR / "snapshot.db"
    try:
        if snap.exists():
            snap.unlink()
        conn.execute("VACUUM INTO ?", (str(snap),))
    except Exception:
        pass  # never let a backup failure abort a tick


def _heartbeat_loop(stop, tick_id):
    """Background beat on its OWN connection, so the tick's heartbeat stays fresh
    while a handler blocks in a long agent subprocess (up to AGENT_TIMEOUT_SEC).
    Without this, an overlapping cron fire would declare a legitimately-busy tick
    crashed at LOCK_STALE_SEC and double-run its ticket."""
    conn = db.connect()
    try:
        while not stop.wait(config.HEARTBEAT_SEC):
            try:
                leasing.heartbeat(conn, tick_id)
            except Exception:
                pass  # a missed beat is recoverable; the next one refreshes
    finally:
        conn.close()


def run_tick():
    tick_id = uuid.uuid4().hex[:12]
    conn = db.init_db()  # idempotent; safe if init wasn't run first
    cfg = config

    if _kill_engaged(conn):
        db.append_audit(conn, "cron", "halted", "kill switch engaged; tick aborted",
                        tick_id=tick_id)
        print(f"[tick {tick_id}] kill switch engaged; aborting")
        return

    if not leasing.acquire_tick_lock(conn, tick_id):
        print(f"[tick {tick_id}] another tick is running; exiting")
        return

    ctx = Ctx(conn, cfg, tick_id)
    db.append_audit(conn, "cron", "tick_start", f"tick {tick_id} started", tick_id=tick_id)
    advanced = 0
    hb_stop = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(hb_stop, tick_id), daemon=True).start()
    try:
        leasing.heartbeat(conn, tick_id)
        leasing.reclaim_expired(conn, tick_id)
        git_ops.reap_worktrees(ctx)
        triage.triage_new(ctx)
        scoring.score_unscored(ctx)

        rows = conn.execute(
            "SELECT * FROM ticket WHERE status='active'"
            " AND id NOT IN (SELECT ticket_id FROM lease)"
            " ORDER BY score IS NULL, score DESC, created_at ASC LIMIT ?",
            (cfg.MAX_TICKETS_PER_TICK,)).fetchall()

        for t in rows:
            if _tick_tokens(conn, tick_id) >= cfg.TICK_BUDGET_TOKENS:
                db.append_audit(conn, "cron", "budget_halt",
                                f"tick budget {cfg.TICK_BUDGET_TOKENS:,} tokens reached;"
                                " stopping selection", tick_id=tick_id)
                break
            if _kill_engaged(conn):
                db.append_audit(conn, "cron", "halted", "kill switch flipped mid-tick",
                                tick_id=tick_id)
                break
            if not leasing.acquire_lease(conn, t["id"], tick_id):
                continue
            # Fence this claim: a pause/close from the web process bumps claim_epoch,
            # making the stage's remaining writes raise StaleEpoch instead of
            # overwriting the human's decision (same contract as remote workers).
            ctx.epoch = conn.execute("SELECT claim_epoch FROM ticket WHERE id=?",
                                     (t["id"],)).fetchone()["claim_epoch"]
            try:
                handler = get_handler(t["type"])
                before = conn.execute("SELECT sub_stage, status FROM ticket WHERE id=?",
                                      (t["id"],)).fetchone()
                try:
                    result = _process_one(ctx, t, handler)
                except StaleEpoch as e:  # human paused/closed it mid-stage — clean abandon
                    result = f"abandoned: {e}"
                    db.append_audit(conn, "cron", "abandoned", str(e)[:300],
                                    ticket_id=t["id"], tick_id=tick_id)
                except Exception as e:  # one bad ticket must not kill the tick
                    result = f"error: {e}"
                    db.append_audit(conn, "cron", "error", str(e)[:300],
                                    ticket_id=t["id"], tick_id=tick_id)
                after = conn.execute("SELECT * FROM ticket WHERE id=?", (t["id"],)).fetchone()
                progressed = (after["status"] in ("done", "failed", "blocked")
                              or after["sub_stage"] != before["sub_stage"]
                              or after["status"] != before["status"])
                _account_attempt(ctx, after, progressed)
                advanced += 1
                print(f"[tick {tick_id}] #{t['id']} ({t['type']}): {result}")
            finally:
                ctx.epoch = None
                leasing.release_lease(conn, t["id"])

        _backup(conn)
    finally:
        hb_stop.set()
        tokens = _tick_tokens(conn, tick_id)
        leasing.finish_tick(conn, tick_id, advanced, tokens)
        db.append_audit(conn, "cron", "tick_end",
                        f"tick {tick_id} advanced {advanced} ticket(s), used {tokens:,} tokens",
                        tick_id=tick_id)
        print(f"[tick {tick_id}] done: advanced {advanced}, used {tokens:,} tokens")
