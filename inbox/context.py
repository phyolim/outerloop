"""The context threaded through a stage. Every state-WRITE a handler performs goes
through ctx.write(op, **kw) — the single seam that makes distribution possible.

Local mode (Ctx) runs the exact SQL the helpers used before, under the same BEGIN
IMMEDIATE boundary. RemoteCtx (worker side) POSTs the same named ops to the hub,
which runs them through these very ops with an epoch check. The epoch fence is
enforced inside the op's transaction so a reclaimed worker's writes can't slip
through a check-then-write race (the hub is multi-threaded)."""

import json

from . import config, db, notify


class StaleEpoch(Exception):
    """A fenced write was rejected: this worker's lease was reclaimed (a newer
    claim_epoch exists). The worker abandons the stage; the hub re-routes it."""


def _fence(conn, ticket_id, epoch):
    """Reject the write if the caller's epoch is not the ticket's current claim_epoch.
    epoch is None for in-process (local) writes, which are never fenced."""
    if epoch is None or ticket_id is None:
        return
    row = conn.execute("SELECT claim_epoch FROM ticket WHERE id=?", (ticket_id,)).fetchone()
    cur = row["claim_epoch"] if row else None
    if cur != epoch:
        raise StaleEpoch(f"ticket {ticket_id}: epoch {epoch} != current {cur}")


def _keep_operator_notes(conn, ticket_id, hs_json):
    """A worker writes back the WHOLE handler_state it read at stage start. An
    operator note appended (via /ui/comment) while the stage ran would be silently
    clobbered — and never reach the worker. Re-merge missing notes here, inside the
    op's transaction, the single seam every hs writeback passes through."""
    row = conn.execute("SELECT handler_state FROM ticket WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        return hs_json
    try:
        cur = json.loads(row["handler_state"] or "{}")
        new = json.loads(hs_json or "{}")
    except ValueError:
        return hs_json
    notes = [c for c in cur.get("clarifications", []) if c.get("q") == "(operator note)"]
    have = {(c.get("q"), c.get("a")) for c in new.get("clarifications", [])}
    missing = [c for c in notes if (c.get("q"), c.get("a")) not in have]
    if not missing:
        return hs_json
    new.setdefault("clarifications", []).extend(missing)
    return json.dumps(new)


def _set_stage(ctx, k):
    conn = ctx.conn
    with db.immediate(conn):
        _fence(conn, k["ticket_id"], k.get("epoch"))
        hs = _keep_operator_notes(conn, k["ticket_id"], k["handler_state"])
        conn.execute(
            "UPDATE ticket SET status=?, sub_stage=?, handler_state=?, pin=COALESCE(?, pin),"
            " blocked_by_decision_id=NULL, updated_at=datetime('now') WHERE id=?",
            (k["status"], k["sub_stage"], hs, k.get("pin"), k["ticket_id"]))
        db.append_audit(conn, k["actor"], k["action"], k["reason"], ticket_id=k["ticket_id"],
                        tick_id=ctx.tick_id, from_stage=k.get("from_stage"),
                        to_stage=k.get("to_stage"), detail=k.get("detail"))
    return {"ok": True}


def _save_hs(ctx, k):
    conn = ctx.conn
    with db.immediate(conn):
        _fence(conn, k["ticket_id"], k.get("epoch"))
        hs = _keep_operator_notes(conn, k["ticket_id"], k["handler_state"])
        conn.execute("UPDATE ticket SET handler_state=?, updated_at=datetime('now') WHERE id=?",
                     (hs, k["ticket_id"]))
        db.append_audit(conn, k["actor"], k["action"], k["reason"], ticket_id=k["ticket_id"],
                        tick_id=ctx.tick_id, detail=k.get("detail"))
    return {"ok": True}


def _fail(ctx, k):
    conn = ctx.conn
    with db.immediate(conn):
        _fence(conn, k["ticket_id"], k.get("epoch"))
        conn.execute("UPDATE ticket SET status='failed', updated_at=datetime('now') WHERE id=?",
                     (k["ticket_id"],))
        db.append_audit(conn, k["actor"], "failed", k["reason"], ticket_id=k["ticket_id"],
                        tick_id=ctx.tick_id, to_stage="failed")
    return {"ok": True}


def _set_repo_path(ctx, k):
    conn = ctx.conn
    with db.immediate(conn):
        _fence(conn, k["ticket_id"], k.get("epoch"))
        conn.execute("UPDATE ticket SET repo_path=?, updated_at=datetime('now') WHERE id=?",
                     (k["repo_path"], k["ticket_id"]))
        db.append_audit(conn, "handler:coding", "repo_path_set",
                        f"repo_path -> {k['repo_path']}", ticket_id=k["ticket_id"],
                        tick_id=ctx.tick_id)
    return {"ok": True}


def _append_audit(ctx, k):
    conn = ctx.conn
    with db.immediate(conn):
        _fence(conn, k.get("ticket_id"), k.get("epoch"))
        db.append_audit(conn, k["actor"], k["action"], k["reason"], ticket_id=k.get("ticket_id"),
                        tick_id=ctx.tick_id, from_stage=k.get("from_stage"),
                        to_stage=k.get("to_stage"), detail=k.get("detail"))
    return {"ok": True}


def _agent_run(ctx, k):
    conn = ctx.conn
    with db.immediate(conn):
        _fence(conn, k["ticket_id"], k.get("epoch"))
        conn.execute(
            "INSERT INTO agent_run(id, ticket_id, role, tick_id, session_id, prompt,"
            " model, worktree_path, exit_code, timed_out, output_json, tokens_in, tokens_out)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (k["session_id"], k["ticket_id"], k["role"], ctx.tick_id, k["session_id"],
             k["prompt"], k.get("model"), k.get("worktree_path"), k.get("exit_code"),
             k.get("timed_out", 0), k["output_json"],
             k.get("tokens_in", 0), k.get("tokens_out", 0)))
        db.append_audit(conn, k["actor"], "agent_run", k["reason"], ticket_id=k["ticket_id"],
                        tick_id=ctx.tick_id, detail=k.get("detail"))
    return {"ok": True}


AGENT_EVENT_CAP = 2000  # rolling live-feed window; agent_run.output_json is the durable record


def _agent_event(ctx, k):
    conn = ctx.conn
    with db.immediate(conn):
        _fence(conn, k["ticket_id"], k.get("epoch"))
        conn.execute(
            "INSERT INTO agent_event(ticket_id, session_id, role, kind, body)"
            " VALUES(?,?,?,?,?)",
            (k["ticket_id"], k["session_id"], k["role"], k["kind"], k["body"]))
        conn.execute("DELETE FROM agent_event WHERE id <= (SELECT MAX(id) - ? FROM agent_event)",
                     (AGENT_EVENT_CAP,))
    return {"ok": True}


def _require(ctx, k):
    conn = ctx.conn
    with db.immediate(conn):
        _fence(conn, k["ticket_id"], k.get("epoch"))
        cur = conn.execute(
            "INSERT INTO decision(ticket_id, kind, question, context, resume_stage)"
            " VALUES(?,?,?,?,?)",
            (k["ticket_id"], k["kind"], k["question"], k["context"], k["resume_stage"]))
        did = cur.lastrowid
        conn.execute("UPDATE ticket SET status='blocked', blocked_by_decision_id=?,"
                     " pin=COALESCE(?, pin), updated_at=datetime('now') WHERE id=?",
                     (did, k.get("pin"), k["ticket_id"]))
        db.append_audit(conn, "gate", "gated", f"{k['kind']}: {k['question']}",
                        ticket_id=k["ticket_id"], tick_id=ctx.tick_id, to_stage="blocked",
                        detail={"decision_id": did, "kind": k["kind"]})
        trow = conn.execute("SELECT title FROM ticket WHERE id=?", (k["ticket_id"],)).fetchone()
    # After commit: this op runs on the hub in both modes (workers POST it here), so one
    # hook covers every decision. Off-thread + best-effort inside notify.send.
    notify.send(conn, f"Decision needed: {k['kind']}",
                f"#{k['ticket_id']} {trow['title'] if trow else ''}\n{k['question']}")
    return {"decision_id": did}


_LOCAL_OPS = {
    "set_stage": _set_stage, "save_hs": _save_hs, "fail": _fail,
    "append_audit": _append_audit, "agent_run": _agent_run, "agent_event": _agent_event,
    "require": _require,
    "set_repo_path": _set_repo_path,
}


def apply_op(conn, op, kw, tick_id="hub"):
    """Run a named write op against `conn` (used by the API to serve a worker's POST).
    The epoch in kw is checked inside the op's transaction."""
    return _LOCAL_OPS[op](_Bare(conn, tick_id), kw)


class _Bare:
    """Minimal ctx the ops need: a conn + a tick_id."""
    def __init__(self, conn, tick_id):
        self.conn = conn
        self.tick_id = tick_id


class Ctx:
    """Local context: handlers' writes execute against the in-process SQLite conn."""

    def __init__(self, conn, cfg, tick_id):
        self.conn = conn
        self.cfg = cfg
        self.tick_id = tick_id

    def write(self, op, **kw):
        return _LOCAL_OPS[op](self, kw)

    def renew(self):
        return {"ok": True}  # single-box: no remote lease to re-verify


class RemoteCtx:
    """Worker context: the SAME handler code runs here, but each write becomes an
    epoch-stamped POST to the coordinator. Has no DB connection — handlers never
    read the DB directly (they use the claimed ticket row + handler_state)."""

    def __init__(self, base_url, token, ticket_id, epoch, tick_id):
        self.base_url = base_url
        self.token = token
        self.ticket_id = ticket_id
        self.epoch = epoch
        self.tick_id = tick_id
        self.cfg = config  # FAKE flag, binary paths, caps — handlers/agent read these
        self.conn = None

    def write(self, op, **kw):
        from . import client
        kw.setdefault("ticket_id", self.ticket_id)
        kw["epoch"] = self.epoch
        try:
            return client.post(self.base_url, "/api/op", {"op": op, **kw}, token=self.token)
        except client.APIError as e:
            if e.code == 409:
                raise StaleEpoch(str(e))
            raise

    def renew(self):
        """Re-verify the lease just before an irreversible effect; raises StaleEpoch
        (-> the worker aborts before acting) if it was reclaimed."""
        from . import client
        try:
            return client.post(self.base_url, "/api/lease/renew",
                               {"ticket_id": self.ticket_id, "epoch": self.epoch}, token=self.token)
        except client.APIError as e:
            if e.code == 409:
                raise StaleEpoch(str(e))
            raise
