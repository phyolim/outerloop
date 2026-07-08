"""The coding delivery lifecycle, one bounded stage per cron tick:
seed -> groomed -> implemented -> reviewing <-> fixing -> opening_pr -> merge_gate
-> merging -> merged -> done (deploy is manual in v0; no executor, no gate).

Review runs on the LOCAL branch diff, so the PR is opened only once the work is
reviewed and stable (opening_pr) — never as a half-done draft. Mid-work the author
may pause to ask the human a question (a 'clarification' block) and resume with the
answer threaded back in.

Author != reviewer is structural: each agent run is assigned its own session UUID,
the reviewer runs on a strictly later tick fed only the diff (never the author's
transcript), and the reviewer has no code path to merge. The review<->fix loop is
hard-capped; merge is always human-gated."""

from .. import agent, db, gate, git_ops, taxonomy
from . import base


def _kind_hint(ticket):
    """One-line steer for the groomer/author based on the ticket's kind — feature/bug/
    chore all route here, so this is where they diverge (e.g. a bug asks for a failing
    test first). See outerloop/taxonomy.py KIND_META."""
    return taxonomy.meta(taxonomy.normalize_kind(ticket["kind"], ticket["type"]))["hint"]


class CodingHandler(base.Handler):
    type = "coding"

    def advance(self, ctx, ticket):
        hs = db.hstate(ticket)
        sub = ticket["sub_stage"] or "seed"
        method = getattr(self, f"_stage_{sub}", None)
        if method is None:
            base.fail(ctx, ticket, f"unknown coding sub_stage '{sub}'")
            return f"failed: unknown stage {sub}"
        return method(ctx, ticket, hs)

    # seed -> groomed: expand the idea into tasks + acceptance criteria (read-only, auto).
    def _stage_seed(self, ctx, ticket, hs):
        res = agent.run_agent(ctx, "groomer", ticket_id=ticket["id"],
                              prompt=f"Expand into tasks + acceptance criteria.\n"
                                     f"{_kind_hint(ticket)}\n"
                                     f"TITLE: {ticket['title']}\nBODY: {ticket['body']}")
        if not base.note_agent(ctx, ticket, hs, res):
            return "failed: groom timed out"
        hs["groom"] = res["data"]
        base.set_stage(ctx, ticket, "groomed", hs, "groomed",
                       "expanded idea into tasks + acceptance criteria")
        return "groomed"

    # creating_repo -> groomed: resume target after a human approves repo creation. Creates
    # the repo, pins repo_path onto the ticket, and routes back to groomed (which now finds
    # a repo_path and proceeds to author). Reject -> fail (can't code without a repo).
    def _stage_creating_repo(self, ctx, ticket, hs):
        url, err = git_ops.create_repo(ctx, ticket)
        if err:
            base.fail(ctx, ticket, f"repo creation failed: {err}")
            return "repo creation failed -> error"
        # Record the canonical URL, not this machine's clone path — any worker that
        # claims a later stage resolves its own local clone (git_ops.local_repo).
        ctx.write("set_repo_path", ticket_id=ticket["id"], repo_path=url)
        base.set_stage(ctx, ticket, "groomed", hs, "repo_created",
                       f"created private repo {url}; continuing")
        return f"created repo {url}"

    # groomed -> implemented: author writes code in an isolated worktree on a fresh branch.
    # A ticket with no repo_path first passes the human-gated repo-creation step below.
    def _stage_groomed(self, ctx, ticket, hs):
        if not ctx.cfg.FAKE and not ticket["repo_path"]:
            name = git_ops.repo_slug(ticket)
            gate.require(ctx, ticket, "create_repo",
                         f"No repo set for this ticket. Create new private GitHub repo '{name}'?",
                         {"repo_name": name}, resume_stage="creating_repo")
            return "new repo gated"
        # Resolve repo_path to THIS machine's clone (cloning a URL on demand). The
        # ticket's repo_path stays canonical/machine-independent — never rewritten to
        # a local path, which would strand every other worker in the fleet.
        repo, err = git_ops.local_repo(ctx, ticket)
        if err:
            base.fail(ctx, ticket, f"repo unavailable: {err}")
            return "repo unavailable -> error"
        branch = hs.get("branch") or git_ops.new_branch(ticket["id"])
        hs["branch"] = branch
        wt, err = git_ops.create_worktree(ctx, ticket, branch, repo)
        if err:
            base.fail(ctx, ticket, f"worktree creation failed: {err}")
            return "worktree creation failed -> error"
        hs["worktree_path"] = str(wt)
        hs.setdefault("base_branch", git_ops.repo_head(ctx, repo))
        crit = (hs.get("groom") or {}).get("acceptance_criteria", [])
        clars = hs.get("clarifications", [])
        answered = "".join(f"\n  Q: {c['q']}\n  A: {c['a']}" for c in clars)
        # Bash included so the author can iterate: run tests, start a dev server,
        # check output. ponytail: full shell shares the user's git/gh creds — an
        # injection-risk tradeoff accepted deliberately; scope with Bash(...) patterns
        # if it bites. Orchestrator still owns commit/push/PR, so tell the agent.
        res = agent.run_agent(ctx, "author", ticket_id=ticket["id"], cwd=wt,
                              worktree_path=wt, allowed_tools="Edit,Write,Bash",
                              prompt=f"Implement this on branch {branch}.\n"
                                     f"{_kind_hint(ticket)}\n"
                                     f"You may use the shell to run/test your work, but do NOT"
                                     f" git commit/push or use gh — the orchestrator handles"
                                     f" commit, push, and the PR after you finish.\n"
                                     f"TITLE: {ticket['title']}\nBODY: {ticket['body']}\n"
                                     f"ACCEPTANCE: {crit}"
                                     + (f"\nEARLIER CLARIFICATIONS (already answered — do NOT"
                                        f" re-ask):{answered}" if clars else ""))
        if not base.note_agent(ctx, ticket, hs, res):
            return "failed: author timed out"
        # The author can pause to ask ONE question; block for a human answer and resume
        # here with it threaded back. Capped so it can't loop forever unattended — an
        # author STILL asking past the cap produced no code, so fail rather than
        # silently marking an empty diff 'implemented'.
        q = res["data"].get("question")
        # Gap-closer: the author may ignore the JSON contract and reply in prose. Only
        # when the result carries NO completion signal (a bare {"text": ...} that never
        # parsed into the expected shape) and reads as a question do we route it to a
        # clarification — so a real completion that merely contains a '?' isn't hijacked.
        d = res["data"]
        if (not q and not any(k in d for k in ("summary", "files_changed", "diff_stat"))
                and isinstance(d.get("text"), str) and "?" in d["text"]):
            q = d["text"].strip()[:1000]
        if q:
            # The cap counts genuine Q&A rounds only — operator notes share the
            # clarifications channel but are steering, not answers to author questions;
            # counting them would hard-fail the author's FIRST real question.
            asked = [c for c in clars if c.get("q") != "(operator note)"]
            if len(asked) >= ctx.cfg.MAX_CLARIFICATIONS:
                base.fail(ctx, ticket,
                          f"author still needs clarification after {len(asked)} answers")
                return "failed: clarification cap"
            base.save_hs(ctx, ticket, hs, "awaiting_clarification",
                         "author needs input; saving progress before blocking")
            gate.require(ctx, ticket, "clarification", q,
                         {"asked_by": "author", "progress": res["data"].get("summary", "")},
                         resume_stage="groomed", pin=ticket["assigned_worker"])
            return "author asked for clarification -> decision queue"
        git_ops.commit_all(ctx, ticket, hs, f"ticket #{ticket['id']}: {ticket['title']}")
        hs["author_session_id"] = res["session_id"]
        hs["diff_stat"] = res["data"].get("diff_stat", "")
        # Pin to the box that now holds the worktree: every later stage (fixing,
        # reviewing, merging) needs that local checkout, so it must route back here.
        base.set_stage(ctx, ticket, "implemented", hs, "implemented",
                       f"author wrote code on branch {branch}",
                       detail={"author_session_id": res["session_id"]},
                       pin=ticket["assigned_worker"])
        return "implemented"

    # implemented -> reviewing: a no-op flip on its OWN tick, so the reviewer is guaranteed
    # to run later than the author with fresh context. No PR yet — review is on the branch.
    def _stage_implemented(self, ctx, ticket, hs):
        hs.setdefault("review_round", 0)
        base.set_stage(ctx, ticket, "reviewing", hs, "ready_for_review",
                       "code written; a separate reviewer runs next tick on the branch diff")
        return "ready for review"

    # reviewing: a SEPARATE agent (new session id, != author) reviews the branch diff only.
    def _stage_reviewing(self, ctx, ticket, hs):
        rounds_used = hs.get("review_round", 0)
        diff = git_ops.branch_diff(ctx, ticket, hs)
        crit = (hs.get("groom") or {}).get("acceptance_criteria", [])
        res = agent.run_agent(
            ctx, "reviewer", ticket_id=ticket["id"],
            prompt=f"Review this branch diff against the acceptance criteria. ROUND: {rounds_used}\n"
                   f"ACCEPTANCE: {crit}\nDIFF:\n{diff}")
        if not base.note_agent(ctx, ticket, hs, res):
            return "failed: review timed out"
        hs["reviewer_session_id"] = res["session_id"]
        verdict = res["data"].get("verdict", "request_changes")
        hs["last_verdict"] = verdict
        if verdict == "approve":
            base.set_stage(ctx, ticket, "opening_pr", hs, "review_approved",
                           "reviewer approved; opening PR now that the work is stable")
            return "approved -> opening PR"
        if rounds_used + 1 >= ctx.cfg.MAX_REVIEW_ROUNDS:
            gate.require(ctx, ticket, "review_exhausted",
                         f"Review still failing after {rounds_used + 1} rounds. Override and open the PR, or reject?",
                         {"diff_stat": hs.get("diff_stat"), "findings": res["data"].get("findings", [])},
                         resume_stage="opening_pr")
            return "review exhausted -> decision queue"
        hs["review_round"] = rounds_used + 1
        hs["last_findings"] = res["data"].get("findings", [])  # so the fixer actually sees them
        base.set_stage(ctx, ticket, "fixing", hs, "review_requested_changes",
                       f"reviewer requested changes (round {rounds_used + 1}/{ctx.cfg.MAX_REVIEW_ROUNDS})",
                       detail={"findings": res["data"].get("findings", [])})
        return "changes requested -> fixing"

    # fixing -> reviewing: the fixer (may reuse author identity; only APPROVAL is forbidden).
    def _stage_fixing(self, ctx, ticket, hs):
        findings = hs.get("last_findings", [])
        res = agent.run_agent(ctx, "fixer", ticket_id=ticket["id"],
                              cwd=hs.get("worktree_path"),
                              worktree_path=hs.get("worktree_path"),
                              allowed_tools="Edit,Write,Bash",  # shell for iteration, same as author
                              prompt=f"Address the reviewer findings.\nFINDINGS: {findings}\n"
                                     f"You may use the shell to run/test your work, but do NOT"
                                     f" git commit/push or use gh — the orchestrator handles that.")
        if not base.note_agent(ctx, ticket, hs, res):
            return "failed: fix timed out"
        git_ops.commit_all(ctx, ticket, hs, f"ticket #{ticket['id']}: address review findings")
        base.set_stage(ctx, ticket, "reviewing", hs, "fixed",
                       f"fixer addressed findings (round {hs.get('review_round', 0)})")
        return "fixed -> reviewing"

    # opening_pr -> merge_gate: push + open the PR now that the work is reviewed and stable.
    # Reality-checked + effect-marked for idempotency (a retried tick won't double-create).
    def _stage_opening_pr(self, ctx, ticket, hs):
        if hs.get("pr_number"):  # reality-check: PR already exists, don't double-create
            base.set_stage(ctx, ticket, "merge_gate", hs, "pr_reused",
                           f"PR #{hs['pr_number']} already open")
            return "pr already open"
        hs["pending_action"] = {"kind": "pr_create", "branch": hs.get("branch")}
        base.save_hs(ctx, ticket, hs, "effect_pending", "about to open PR")
        num, url = git_ops.open_pr(ctx, ticket, hs)
        if not num:
            # Never enter merge_gate with pr_number=None — every downstream gh call
            # would degrade confusingly. No state change => the attempts ceiling
            # retries this stage and eventually fails the ticket.
            ctx.write("append_audit", ticket_id=ticket["id"], actor="handler:coding",
                      action="pr_create_failed",
                      reason=f"gh pr create returned no PR number for branch {hs.get('branch')}")
            return "pr create failed (will retry)"
        hs.pop("pending_action", None)
        hs["pr_number"], hs["pr_url"] = num, url
        base.set_stage(ctx, ticket, "merge_gate", hs, "pr_opened",
                       f"opened PR #{num} (reviewed & stable)", detail={"pr_url": url})
        return f"opened PR #{num} -> merge gate"

    # merge_gate: ALWAYS queue in v0. The PR + checks are shown in the decision card.
    def _stage_merge_gate(self, ctx, ticket, hs):
        ok, status = git_ops.checks_green(ctx, ticket, hs)
        gate.require(ctx, ticket, "merge",
                     f"Merge PR #{hs.get('pr_number')}?",
                     {"pr_url": hs.get("pr_url"), "diff_stat": hs.get("diff_stat"),
                      "checks": status, "checks_green": ok},
                     resume_stage="merging")
        return "merge gated"

    # merging: the resume target after a human approves. Green CI is a HARD precondition
    # here, independent of the human's click (must-fix #3) — EXCEPT the no-CI case:
    # the merge gate already showed "no CI checks configured" and the human approved
    # it, and no-CI can never turn green, so re-gating would just re-ask the same
    # unanswerable question forever. Actually-failing checks still block.
    def _stage_merging(self, ctx, ticket, hs):
        if git_ops.pr_state(ctx, ticket, hs) == "merged":  # idempotent
            base.set_stage(ctx, ticket, "merged", hs, "already_merged", "PR already merged")
            return "already merged"
        ok, status = git_ops.checks_green(ctx, ticket, hs)
        if not ok and not status.startswith(git_ops.NO_CI_STATUS):
            gate.require(ctx, ticket, "merge",
                         f"Checks are not green ({status}). Re-approve merge of PR #{hs.get('pr_number')}?",
                         {"pr_url": hs.get("pr_url"), "checks": status, "checks_green": False},
                         resume_stage="merging")
            return f"merge blocked: {status}"
        hs["pending_action"] = {"kind": "merge", "pr_number": hs.get("pr_number")}
        base.save_hs(ctx, ticket, hs, "effect_pending", "about to merge")
        ctx.renew()  # must-fix #6: re-verify the lease right before the irreversible merge
        if not git_ops.merge_pr(ctx, ticket, hs):
            # A broken action is an error, not a go/no-go — fail it so the UI shows the
            # error (with a Retry that re-enters this stage), never approve/reject.
            base.fail(ctx, ticket, f"gh merge failed for PR #{hs.get('pr_number')}")
            return "merge command failed -> error"
        hs.pop("pending_action", None)
        git_ops.cleanup_worktree(ctx, ticket, hs)
        base.set_stage(ctx, ticket, "merged", hs, "merged",
                       f"merged PR #{hs.get('pr_number')} (squash); worktree cleaned up")
        return "merged"

    # merged -> done: v0 has no deploy executor, so there is nothing to gate — a
    # deploy decision would be a mandatory no-op second approval per ticket. Deploy
    # stays manual; the gate returns when a real executor is wired.
    def _stage_merged(self, ctx, ticket, hs):
        base.finish(ctx, ticket, "merged; deploy is manual in v0")
        return "done"

    # Resume target for deploy decisions gated before the deploy gate was removed.
    def _stage_deployed(self, ctx, ticket, hs):
        base.finish(ctx, ticket, "deploy is manual in v0; ticket complete")
        return "done"

    def on_rework(self, ctx, ticket, decision):
        # merge/review_exhausted re-run stages that would EXECUTE (merge) or re-review
        # blind — route to the fixer instead, with the human's note as the findings.
        # Everything else keeps the base behavior: re-run the current stage, which
        # re-drafts/re-asks with the note threaded into the prompt.
        if decision["kind"] in ("merge", "review_exhausted"):
            hs = db.hstate(ticket)
            hs["last_findings"] = [f"operator requested changes: {decision['answer_note'] or ''}"]
            base.set_stage(ctx, ticket, "fixing", hs, "rework_requested",
                           "human requested changes; back to fixing")
        else:
            super().on_rework(ctx, ticket, decision)

    def on_reject(self, ctx, ticket, decision):
        hs = db.hstate(ticket)
        kind = decision["kind"]
        if kind == "clarification":
            base.fail(ctx, ticket, "author needed clarification and human declined to answer")
        elif kind == "create_repo":
            base.fail(ctx, ticket, "no repo set and human declined creating one")
        elif kind == "deploy":
            base.finish(ctx, ticket, "merged; deploy declined by human")
        elif kind == "review_exhausted":
            base.fail(ctx, ticket, "review exhausted and human rejected override")
        elif kind == "merge":
            # Reject = stop (the UI says so; "Request changes" is the loop path).
            # Retry from the failed card re-enters merge_gate if the human changes
            # their mind. The note lands in the fail reason so it isn't lost.
            note = decision["answer_note"] or ""
            base.fail(ctx, ticket, "human rejected merge" + (f": {note}" if note else ""))
        else:
            base.finish(ctx, ticket, f"decision '{kind}' rejected", status="done")
