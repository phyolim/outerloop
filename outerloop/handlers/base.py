"""Handler interface + the shared state-transition helpers. tick.py is type-agnostic:
it leases a ticket, calls handler.advance() for ONE bounded stage, and commits.
Handlers never block on a human directly — they call gate.require() and return."""

import json
from abc import ABC, abstractmethod

from .. import db


class Handler(ABC):
    type = None

    @abstractmethod
    def advance(self, ctx, ticket):
        """Perform AT MOST ONE bounded stage. Mutate status/sub_stage/handler_state,
        write audit, and return a short human string. Never loop, never block."""

    def on_reject(self, ctx, ticket, decision):
        """Called when the human rejects a decision this handler raised."""
        finish(ctx, ticket, f"decision '{decision['kind']}' rejected; closing", status="done")

    def on_rework(self, ctx, ticket, decision):
        """Called when the human requests changes instead of approve/reject. The note is
        already threaded into handler_state (tick._resume); by default the ticket re-runs
        its current stage, which re-proposes with the note visible. Override when the
        current stage would EXECUTE the gated action rather than re-draft it."""

    def is_terminal(self, sub_stage):
        return sub_stage == "done"


def set_stage(ctx, ticket, sub_stage, hs, action, reason, *, status="active", detail=None,
              pin=None):
    ctx.write("set_stage", ticket_id=ticket["id"], status=status, sub_stage=sub_stage,
              handler_state=json.dumps(hs), actor=f"handler:{ticket['type']}", action=action,
              reason=reason, from_stage=ticket["sub_stage"], to_stage=sub_stage, detail=detail,
              pin=pin)


def save_hs(ctx, ticket, hs, action, reason, *, detail=None):
    """Persist handler_state WITHOUT changing the stage. Used to write an
    effect-pending marker before an external side effect (must-fix #4)."""
    ctx.write("save_hs", ticket_id=ticket["id"], handler_state=json.dumps(hs),
              actor=f"handler:{ticket['type']}", action=action, reason=reason, detail=detail)


def finish(ctx, ticket, reason, *, status="done"):
    set_stage(ctx, ticket, "done", db.hstate(ticket), "finished", reason, status=status)


def fail(ctx, ticket, reason):
    ctx.write("fail", ticket_id=ticket["id"], actor=f"handler:{ticket['type']}", reason=reason)


def note_agent(ctx, ticket, hs, res):
    """Track consecutive agent timeouts; fail the ticket after the cap (must-fix #5).
    Returns True if it is safe to continue this stage, False if the ticket was failed."""
    if res["timed_out"]:
        hs["consec_timeouts"] = hs.get("consec_timeouts", 0) + 1
        if hs["consec_timeouts"] >= ctx.cfg.MAX_CONSEC_TIMEOUTS:
            fail(ctx, ticket, f"agent timed out {hs['consec_timeouts']}x in a row")
            return False
    else:
        hs["consec_timeouts"] = 0
    return True
