"""Personal ops / life admin: plan -> propose -> [GATE] -> execute -> done. Every
external send/schedule/delete is irreversible, so it is ALWAYS gated with the exact
drafted payload as context. In v0 the post-approval 'execute' is STUBBED to write an
artifact + audit — the gate spine is real, the live send (Gmail/Calendar) is deferred."""

from .. import agent, config, db
from . import base
from .. import gate


class OpsHandler(base.Handler):
    type = "ops"

    def advance(self, ctx, ticket):
        hs = db.hstate(ticket)
        sub = ticket["sub_stage"] or "seed"
        if sub == "seed":
            return self._propose(ctx, ticket, hs)
        if sub == "execute":
            return self._execute(ctx, ticket, hs)
        base.fail(ctx, ticket, f"unknown ops sub_stage '{sub}'")
        return "failed"

    def _propose(self, ctx, ticket, hs):
        # Operator notes (e.g. a rework answer on the gate) steer the re-draft.
        notes = "".join(f"\nOPERATOR NOTE: {c['a']}" for c in hs.get("clarifications", []))
        res = agent.run_agent(ctx, "ops", ticket_id=ticket["id"], ticket=ticket,
                              prompt=f"Draft the concrete action (do NOT send).\n"
                                     f"TITLE: {ticket['title']}\nBODY: {ticket['body']}"
                                     + notes)
        if not base.note_agent(ctx, ticket, hs, res):
            return "failed: propose timed out"
        # A parse miss yields no 'action'; fail rather than hand a human an empty,
        # meaningless "approve ? action" gate card.
        action = res["data"].get("action") or {}
        if not action.get("kind"):
            base.fail(ctx, ticket, "ops agent returned no concrete action to approve")
            return "failed: empty action"
        hs["action"] = action
        base.save_hs(ctx, ticket, hs, "proposed", f"drafted a {action.get('kind','?')} action")
        gate.require(ctx, ticket, "irreversible_action",
                     f"Approve this {action.get('kind','action')}? It will be sent on approval.",
                     {"action": action}, resume_stage="execute")
        return "proposed -> decision queue"

    def _execute(self, ctx, ticket, hs):
        # v0 STUB: write the approved action to an artifact instead of sending it.
        out = config.ARTIFACTS_DIR / str(ticket["id"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "action.json").write_text(__import__("json").dumps(hs.get("action", {}), indent=2))
        base.finish(ctx, ticket,
                    "v0 stub: approved action written to artifact (live send deferred)")
        return "executed (stub)"
