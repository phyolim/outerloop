"""The decision gate: the single choke point every side-effecting action passes
through. Rule is a 2x2 on reversibility x impact, default-deny. Handlers never
execute irreversible effects directly — they call require(), which blocks the
ticket until a human answers in the UI. That makes the gate unbypassable."""

import json

from . import db

# Hard-wired to ALWAYS queue, classifier-independent. These are the actions whose
# blast radius we never let an unattended loop take on its own.
ALWAYS_QUEUE = {"merge", "deploy", "review_exhausted", "irreversible_action",
                "high_impact", "budget_exceeded"}


def classify(reversibility, impact):
    """AUTO iff reversible AND low-impact; else QUEUE. Unknowns default to QUEUE."""
    if reversibility == "reversible" and (impact or 5) <= 3:
        return "auto"
    return "queue"


def require(ctx, ticket, kind, question, context, resume_stage, pin=None):
    """Enqueue a decision and block the ticket, atomically. Returns the decision id.
    Selection skips status='blocked' tickets, so a blocked ticket consumes zero
    work and zero tokens until the human answers. `pin` keeps the ticket on the
    current worker across the block (needed when a live worktree must be reused)."""
    r = ctx.write("require", ticket_id=ticket["id"], kind=kind, question=question,
                  context=json.dumps(context), resume_stage=resume_stage, pin=pin)
    return r["decision_id"]


def expire_orphan_permissions(conn):
    """Permission asks (context._perm_ask) ride inside a live agent run — once the
    ticket's lease is gone nobody is waiting on the answer. Void them so the Inbox
    doesn't collect dead asks and a late 'Allow' click acts on nothing. Normally the
    asking run expires its own (perm_expire); this sweeps runs that were killed."""
    with db.immediate(conn):
        conn.execute(
            "UPDATE decision SET status='rejected', consumed=1, answered_at=datetime('now'),"
            " answer_note='(expired — the agent run ended before this was answered)'"
            " WHERE kind='permission' AND status='pending'"
            " AND ticket_id NOT IN (SELECT ticket_id FROM lease)")


def answered_decision(conn, ticket):
    """The ticket's blocking decision, iff it has been answered and not yet consumed."""
    did = ticket["blocked_by_decision_id"]
    if not did:
        return None
    return conn.execute(
        "SELECT * FROM decision WHERE id=? AND status IN ('approved','rejected')"
        " AND consumed=0", (did,)).fetchone()


def consume(conn, decision_id):
    """Mark a decision consumed and clear the ticket's block pointer. Guards a
    duplicate resume from running the gated action twice."""
    conn.execute("UPDATE decision SET consumed=1 WHERE id=?", (decision_id,))
