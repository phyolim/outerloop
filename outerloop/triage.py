"""Cheap junk-triage. Parks low-value tickets (never deletes) before any expensive
work, so the parking lot stays mineable. A keyword heuristic catches obvious junk
for free; a short agent call handles the ambiguous ones."""

import json

from . import agent, db, gate, notify, scoring

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


def _requires_for(t):
    req = t["requires"]
    if not req or req == "[]":
        return json.dumps(REQUIRES_BY_TYPE.get(t["type"], []))
    return req


def _clarify(ctx, t, question, options, req):
    """A real-but-vague ticket: ask the operator instead of parking it. Set requires so a
    capable worker can claim it once answered, then block on a 'clarification' decision —
    the answer threads back and the ticket resumes at its handler's first stage ('seed').
    gate.require opens its own transaction, so this runs outside any db.immediate block."""
    with db.immediate(ctx.conn):
        ctx.conn.execute("UPDATE ticket SET requires=?, updated_at=datetime('now')"
                         " WHERE id=?", (req, t["id"]))
    context = {"asked_by": "triage"}
    if options:
        context["options"] = options
    gate.require(ctx, t, "clarification", question, context, resume_stage="seed")


def triage_new(ctx):
    """Move each inbox ticket to 'active' (worth working), 'parked' (junk), or block it
    on a clarification when it's a real ask that's too vague to act on."""
    conn = ctx.conn
    # draft=1 rows are unsubmitted ideas: invisible to the pipeline until /start flips them.
    for t in conn.execute("SELECT * FROM ticket WHERE status='inbox' AND draft=0").fetchall():
        keep, reason = _heuristic(t)
        clarify_q, clarify_opts = None, None
        # "Too thin" isn't junk — it's a real request missing detail. Ask for the detail
        # (they answer in the thread) instead of silently parking it. Junk markers and
        # empty titles still park; only this narrow case becomes a question.
        if not keep and reason == "too thin to act on":
            clarify_q = ("This request is too brief to act on. What exactly should be done,"
                         " and where? Add any detail a worker would need.")
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
            # A real-but-ambiguous ticket comes back with a question: ask the operator
            # rather than accepting a ticket the worker can't act on. Only meaningful
            # when kept (junk stays junk).
            q = (res["data"].get("question") or "").strip()
            if keep and q:
                clarify_q = q[:1000]
                opts = res["data"].get("options")
                clarify_opts = [str(o) for o in opts] if isinstance(opts, list) and opts else None
            # A timed-out run has no real rating — leave score NULL so score_unscored
            # backfills it with a proper scorer call instead of stamping defaults.
            elif keep and not res["timed_out"]:
                factors = scoring.factors_from_data(res["data"])
        if clarify_q:
            _clarify(ctx, t, clarify_q, clarify_opts, _requires_for(t))
            continue
        with db.immediate(conn):
            if keep:
                req = _requires_for(t)
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
