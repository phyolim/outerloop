"""Knowledge work: research/draft -> fresh-context review -> done. Proves the
dispatch interface end-to-end with no git/gh/worktree. The deliverable is a file
artifact; the audit log is the record. A separate reviewer session keeps the
author!=reviewer spirit (no hard round cap needed here)."""

from .. import agent, config, db
from . import base


class KnowledgeHandler(base.Handler):
    type = "knowledge"

    def advance(self, ctx, ticket):
        hs = db.hstate(ticket)
        sub = ticket["sub_stage"] or "seed"
        if sub == "seed":
            return self._draft(ctx, ticket, hs)
        if sub == "drafted":
            return self._review(ctx, ticket, hs)
        base.fail(ctx, ticket, f"unknown knowledge sub_stage '{sub}'")
        return "failed"

    def _draft(self, ctx, ticket, hs):
        res = agent.run_agent(ctx, "knowledge", ticket_id=ticket["id"], ticket=ticket,
                              prompt=f"Research and draft a deliverable.\n"
                                     f"TITLE: {ticket['title']}\nBODY: {ticket['body']}"
                                     + base.operator_notes(hs))
        if not base.note_agent(ctx, ticket, hs, res):
            return "failed: draft timed out"
        # On a JSON-parse miss `data` has no 'deliverable'; fall back to raw text, and
        # fail loudly rather than write an empty file and "pass" review on nothing.
        deliverable = res["data"].get("deliverable") or res["data"].get("text", "")
        if not deliverable.strip():
            base.fail(ctx, ticket, "knowledge agent returned an empty deliverable")
            return "failed: empty deliverable"
        out = config.ARTIFACTS_DIR / str(ticket["id"])
        out.mkdir(parents=True, exist_ok=True)
        path = out / "draft.md"
        path.write_text(deliverable)
        hs["author_session_id"] = res["session_id"]
        hs["artifact"] = str(path)
        base.set_stage(ctx, ticket, "drafted", hs, "drafted",
                       f"wrote deliverable to {path}", detail={"artifact": str(path)})
        return "drafted"

    def _review(self, ctx, ticket, hs):
        res = agent.run_agent(ctx, "reviewer", ticket_id=ticket["id"], ticket=ticket,
                              prompt=f"Review this deliverable for quality."
                                     f"{base.operator_notes(hs)}\n"
                                     f"{open(hs['artifact']).read()[:4000]}")
        if not base.note_agent(ctx, ticket, hs, res):
            return "failed: review timed out"
        assert res["session_id"] != hs.get("author_session_id"), "reviewer must differ from author"
        base.finish(ctx, ticket, f"reviewed; deliverable ready at {hs.get('artifact')}")
        return "done"
