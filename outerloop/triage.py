"""Cheap junk-triage. Parks low-value tickets (never deletes) before any expensive
work, so the parking lot stays mineable. A keyword heuristic catches obvious junk
for free; a short agent call handles the ambiguous ones."""

import json

from . import agent, db, notify, scoring

# Capability requirements inferred from ticket type, applied only when the
# producer/UI didn't set them. 'coding' needs a dev machine with repo access.
REQUIRES_BY_TYPE = {"coding": ["dev"], "knowledge": [], "ops": []}

# Obvious-junk substrings (case-insensitive). Fast path, no agent call.
# Matched against the TITLE only: a legitimate body like "please delete this old
# feature flag" must not auto-park; body-only junk still reaches the agent call.
_JUNK_MARKERS = ("test junk", "ignore me", "delete this", "asdf", "lorem ipsum")


def _heuristic(t):
    title = (t["title"] or "").strip().lower()
    body = (t["body"] or "").strip()
    if not title:
        return False, "empty title"
    if any(m in title for m in _JUNK_MARKERS):
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
        factors = None  # set only when the agent ran (real mode + heuristic-kept)
        if keep and not ctx.cfg.FAKE:
            # Borderline: let the agent make the call in real mode. The same call also
            # rates the ticket, so a kept ticket needs no separate scorer run — the four
            # factors ride back on the triage result (score_unscored stays as a backfill
            # for anything that reaches 'active' without a score).
            res = agent.run_agent(
                ctx, "triage",
                prompt=f"Is this a real, actionable ticket or junk? If it is real, also"
                       f" rate it (impact/urgency/confidence/effort, each 1..5, plus"
                       f" reversibility and a one-line justification).\n"
                       f"TITLE: {t['title']}\nBODY: {t['body']}",
                ticket_id=t["id"], ticket=t, json_schema="triage",
            )
            keep = bool(res["data"].get("keep", True))
            reason = res["data"].get("reason", reason)
            # A timed-out run has no real rating — leave score NULL so score_unscored
            # backfills it with a proper scorer call instead of stamping defaults.
            if keep and not res["timed_out"]:
                factors = scoring.factors_from_data(res["data"])
        with db.immediate(conn):
            if keep:
                req = t["requires"]
                if not req or req == "[]":
                    req = json.dumps(REQUIRES_BY_TYPE.get(t["type"], []))
                if factors:
                    impact, urgency, confidence, effort, rev, why = factors
                    score = scoring.compute_score(impact, urgency, confidence, effort)
                    conn.execute(
                        "UPDATE ticket SET status='active', sub_stage=NULL, requires=?,"
                        " impact=?, urgency=?, confidence=?, effort=?, score=?,"
                        " reversibility=?, updated_at=datetime('now') WHERE id=?",
                        (req, impact, urgency, confidence, effort, score, rev, t["id"]))
                    db.append_audit(
                        conn, "triage", "accepted",
                        f"{reason}; requires={req}; score {score} ="
                        f" (I{impact} x U{urgency} x C{confidence}) / E{effort}: {why}",
                        ticket_id=t["id"], tick_id=ctx.tick_id,
                        from_stage="inbox", to_stage="active",
                        detail={"impact": impact, "urgency": urgency,
                                "confidence": confidence, "effort": effort})
                else:
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
        if not keep:
            # A parked false-positive dies silently otherwise (hidden by the board's
            # default filter, digest only lasts 24h) — push so it can be revived.
            notify.send(conn, "Ticket parked as junk",
                        f"#{t['id']} {t['title']}\n{reason} — revive from Board › On hold")
