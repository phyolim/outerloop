"""Cheap junk-triage. Parks low-value tickets (never deletes) before any expensive
work, so the parking lot stays mineable. A keyword heuristic catches obvious junk
for free; a short agent call handles the ambiguous ones."""

import json

from . import agent, db

# Capability requirements inferred from ticket type, applied only when the
# producer/UI didn't set them. 'coding' needs a dev machine with repo access.
REQUIRES_BY_TYPE = {"coding": ["dev"], "knowledge": [], "ops": []}

# Obvious-junk substrings (case-insensitive). Fast path, no agent call.
_JUNK_MARKERS = ("test junk", "ignore me", "delete this", "asdf", "lorem ipsum")


def _heuristic(t):
    title = (t["title"] or "").strip().lower()
    body = (t["body"] or "").strip()
    if not title:
        return False, "empty title"
    if any(m in title or m in body.lower() for m in _JUNK_MARKERS):
        return False, "matches junk marker"
    if len(title) < 4 and not body:
        return False, "too thin to act on"
    return True, "passes heuristic"


def triage_new(ctx):
    """Move each inbox ticket to 'active' (worth working) or 'parked' (junk)."""
    conn = ctx.conn
    # draft=1 rows are unsubmitted ideas: invisible to the pipeline until /start flips them.
    for t in conn.execute("SELECT * FROM ticket WHERE status='inbox' AND draft=0").fetchall():
        keep, reason = _heuristic(t)
        if keep and not ctx.cfg.FAKE:
            # Borderline: let the agent make the call in real mode.
            res = agent.run_agent(
                ctx, "triage",
                prompt=f"Is this a real, actionable ticket or junk?\nTITLE: {t['title']}\nBODY: {t['body']}",
                ticket_id=t["id"], json_schema="triage",
            )
            keep = bool(res["data"].get("keep", True))
            reason = res["data"].get("reason", reason)
        with db.immediate(conn):
            if keep:
                req = t["requires"]
                if not req or req == "[]":
                    req = json.dumps(REQUIRES_BY_TYPE.get(t["type"], []))
                conn.execute(
                    "UPDATE ticket SET status='active', sub_stage=NULL, requires=?,"
                    " updated_at=datetime('now') WHERE id=?", (req, t["id"]))
                db.append_audit(conn, "triage", "accepted", f"{reason}; requires={req}",
                                ticket_id=t["id"], tick_id=ctx.tick_id,
                                from_stage="inbox", to_stage="active")
            else:
                conn.execute(
                    "UPDATE ticket SET status='parked', park_reason=?,"
                    " updated_at=datetime('now') WHERE id=?", (reason, t["id"]))
                db.append_audit(conn, "triage", "parked", reason,
                                ticket_id=t["id"], tick_id=ctx.tick_id,
                                from_stage="inbox", to_stage="parked")
