"""Legible prioritization. score = (impact * urgency * confidence) / max(effort,1),
each factor an integer 1..5. The numerator is an AND of 'valuable, timely,
well-understood'; effort floats cheap wins up. Range 0.2 .. 125. Because it's one
line of arithmetic over four user-editable integers, the user can always reconstruct
exactly why ticket A outranks ticket B."""

from . import agent, db


def compute_score(impact, urgency, confidence, effort):
    if None in (impact, urgency, confidence, effort):
        return None
    return round((impact * urgency * confidence) / max(effort, 1), 2)


def breakdown(t):
    s = t["score"]
    if s is None:
        return "unscored"
    return f"score {s} = (I{t['impact']} x U{t['urgency']} x C{t['confidence']}) / E{t['effort']}"


def estimate_factors(ctx, t):
    """Ask the scorer agent for the four 1..5 factors + a one-line justification each.
    The factors are stored RAW so nothing about the priority is a black box."""
    res = agent.run_agent(
        ctx, "scorer",
        prompt=f"Rate this ticket.\nTITLE: {t['title']}\nBODY: {t['body']}",
        ticket_id=t["id"], ticket=t,
        json_schema="scorer",
    )
    d = res["data"]

    def factor(k):  # tolerate a missing/garbled factor rather than crash the tick top-half
        try:
            return max(1, min(5, int(d.get(k, 3))))
        except (TypeError, ValueError):
            return 3

    return (
        factor("impact"), factor("urgency"), factor("confidence"), factor("effort"),
        d.get("reversibility", "reversible"),
        d.get("justification", ""),
    )


def score_unscored(ctx):
    """Score every active ticket that has no score yet, in its own transaction."""
    conn = ctx.conn
    rows = conn.execute(
        "SELECT * FROM ticket WHERE status='active' AND score IS NULL"
    ).fetchall()
    for t in rows:
        impact, urgency, confidence, effort, rev, why = estimate_factors(ctx, t)
        score = compute_score(impact, urgency, confidence, effort)
        reason = f"score {score} = (I{impact} x U{urgency} x C{confidence}) / E{effort}: {why}"
        with db.immediate(conn):
            conn.execute(
                "UPDATE ticket SET impact=?, urgency=?, confidence=?, effort=?,"
                " score=?, reversibility=?, updated_at=datetime('now') WHERE id=?",
                (impact, urgency, confidence, effort, score, rev, t["id"]),
            )
            db.append_audit(conn, "scorer", "scored", reason,
                            ticket_id=t["id"], tick_id=ctx.tick_id,
                            detail={"impact": impact, "urgency": urgency,
                                    "confidence": confidence, "effort": effort})
