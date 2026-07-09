"""The coding delivery lifecycle, one bounded stage per cron tick:
seed -> groomed -> implemented -> reviewing <-> fixing -> opening_pr -> merge_gate
-> merging -> merged -> done (deploy is manual in v0; no executor, no gate). A merge
GitHub refuses because the PR conflicts with base detours merging -> resolving_conflicts
(an agent merges base into the branch, resolves, pushes) -> merge_gate (re-approval).

Review runs on the LOCAL branch diff, so the PR is opened only once the work is
reviewed and stable (opening_pr) — never as a half-done draft. Mid-work the author
may pause to ask the human a question (a 'clarification' block) and resume with the
answer threaded back in.

Author != reviewer is structural: each agent run is assigned its own session UUID,
the reviewer runs on a strictly later tick fed only the diff (never the author's
transcript), and the reviewer has no code path to merge. The review<->fix loop is
hard-capped; merge is always human-gated."""

from .. import agent, config, db, gate, git_ops, taxonomy
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
        res = agent.run_agent(ctx, "groomer", ticket_id=ticket["id"], ticket=ticket,
                              prompt=f"Expand into tasks + acceptance criteria.\n"
                                     f"{_kind_hint(ticket)}\n"
                                     f"TITLE: {ticket['title']}\nBODY: {ticket['body']}"
                                     + base.operator_notes(hs))
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
        # "Agent does, code verifies": the AUTHOR sets up its own workspace (clone the
        # canonical repo_path URL if this machine lacks it, add the worktree) because
        # adapting to a messy environment is what the agent is good at — the code only
        # picks the deterministic names (branch, worktree path) and verifies afterward.
        # repo_path itself stays machine-independent; nothing here rewrites it.
        branch = hs.get("branch") or git_ops.new_branch(ticket["id"])
        hs["branch"] = branch
        wt = git_ops.worktree_path(ticket, branch)
        hs["worktree_path"] = str(wt)
        real = not ctx.cfg.FAKE and ticket["repo_path"]
        if real:
            # cwd must exist at spawn; WORKTREES_DIR also grants Edit/Write inside the
            # worktree the agent is about to create under it.
            config.ensure_dirs()
            cwd = config.WORKTREES_DIR
            clone = config.REPOS_DIR / git_ops.clone_name(ticket["repo_path"])
            setup = (f"FIRST set up the workspace with the shell:\n"
                     f"1. Ensure a clone of {ticket['repo_path']} exists at {clone}"
                     f" (clone it there if missing; reuse it if present).\n"
                     f"2. Ensure the worktree exists: git -C {clone} worktree add"
                     f" {wt} -b {branch}  (reuse it if it already exists).\n"
                     f"3. Do ALL implementation work inside {wt}.\n"
                     f"When finished, COMMIT all your work in {wt} with a descriptive"
                     f" message. Do NOT push, do NOT open or merge PRs, and do NOT"
                     f" touch any branch other than {branch} — shipping is the"
                     f" orchestrator's job.\n\n")
        else:
            wt.mkdir(parents=True, exist_ok=True)
            cwd, setup = wt, ""
        groom = hs.get("groom") or {}
        crit = groom.get("acceptance_criteria", [])
        tasks = groom.get("tasks", [])
        clars = hs.get("clarifications", [])
        answered = "".join(f"\n  Q: {c['q']}\n  A: {c['a']}" for c in clars)
        # Bash included so the author can set up + iterate: clone, run tests, start a
        # dev server. ponytail: full shell shares the user's git/gh creds — the durable
        # main-branch protection is GitHub-side (branch protection / required PRs),
        # not this prompt; the orchestrator's merge gate is the only sanctioned door.
        res = agent.run_agent(ctx, "author", ticket_id=ticket["id"], ticket=ticket, cwd=cwd,
                              worktree_path=wt, allowed_tools="Edit,Write,Bash",
                              prompt=f"{setup}Implement this on branch {branch}.\n"
                                     f"{_kind_hint(ticket)}\n"
                                     f"TITLE: {ticket['title']}\nBODY: {ticket['body']}\n"
                                     f"TASKS: {tasks}\nACCEPTANCE: {crit}"
                                     + (f"\nEARLIER CLARIFICATIONS (already answered — do NOT"
                                        f" re-ask):{answered}" if clars else ""))
        if not base.note_agent(ctx, ticket, hs, res):
            return "failed: author timed out"
        # A timed-out author left partial (or no) work: don't commit-and-advance it into
        # review — the review<->fix loop would then finish the implementation at
        # reviewer+fixer prices. Persist hs (note_agent bumped consec_timeouts) and
        # retry this stage in place; the setup prompt already reuses an existing worktree,
        # and MAX_CONSEC_TIMEOUTS still fails a stuck author after the cap.
        if res["timed_out"]:
            base.save_hs(ctx, ticket, hs, "author_timeout",
                         "author run timed out; retrying in place, not advancing to review")
            return "author timed out (will retry)"
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
        # Verify, don't trust: the worktree the author claims to have set up must
        # actually be a git worktree on the right branch. Fail loud with the real
        # state — never advance an empty workspace to review.
        base_branch, err = git_ops.verify_worktree(ctx, ticket, hs)
        if err:
            base.fail(ctx, ticket, f"author run left no usable worktree: {err}")
            return "workspace verification failed -> error"
        hs.setdefault("base_branch", base_branch)
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
        # An empty diff means the author committed nothing (or base_branch degraded to
        # HEAD, making the diff HEAD...HEAD). Reviewing nothing wastes a run and risks a
        # blind approve that ships unreviewed — fail loud instead.
        if not diff.strip():
            base.fail(ctx, ticket,
                      "branch diff is empty — nothing to review (check base_branch)")
            return "empty diff -> error"
        # A pathological diff (lockfile churn, vendored deps) would blow one round to
        # hundreds of thousands of tokens, x MAX_REVIEW_ROUNDS. Don't truncate-and-review
        # (a blind approve on a partial diff is worse than the spend) — hand it to a human.
        if len(diff) > ctx.cfg.MAX_REVIEW_DIFF_CHARS:
            gate.require(ctx, ticket, "review_exhausted",
                         f"Diff too large for automated review ({len(diff):,} chars,"
                         f" {hs.get('diff_stat') or 'no diff stat'})."
                         f" Open the PR for manual review, or reject?",
                         {"diff_stat": hs.get("diff_stat")},
                         resume_stage="opening_pr")
            return "diff too large -> decision queue"
        crit = (hs.get("groom") or {}).get("acceptance_criteria", [])
        res = agent.run_agent(
            ctx, "reviewer", ticket_id=ticket["id"], ticket=ticket,
            prompt=f"Review this branch diff against the acceptance criteria. ROUND: {rounds_used}\n"
                   f"ACCEPTANCE: {crit}{base.operator_notes(hs)}\nDIFF:\n{diff}")
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
        crit = (hs.get("groom") or {}).get("acceptance_criteria", [])
        res = agent.run_agent(ctx, "fixer", ticket_id=ticket["id"], ticket=ticket,
                              cwd=hs.get("worktree_path"),
                              worktree_path=hs.get("worktree_path"),
                              allowed_tools="Edit,Write,Bash",  # shell for iteration, same as author
                              # Same brief the author got (title/body/acceptance) so the fixer
                              # doesn't re-derive intent from the worktree at model prices — a
                              # misread finding costs a full extra review+fix round.
                              prompt=f"Address the reviewer findings on this ticket.\n"
                                     f"TITLE: {ticket['title']}\nBODY: {ticket['body']}\n"
                                     f"ACCEPTANCE: {crit}\nFINDINGS: {findings}\n"
                                     f"You may use the shell to run/test your work. COMMIT your"
                                     f" changes here when done. Do NOT push and do NOT open or"
                                     f" merge PRs — shipping is the orchestrator's job."
                                     + base.operator_notes(hs))
        if not base.note_agent(ctx, ticket, hs, res):
            return "failed: fix timed out"
        # Timed-out fixer: don't commit whatever is lying around and bounce back to the
        # reviewer (that pays a reviewer round on half-applied fixes). Persist hs
        # (consec_timeouts) and retry fixing in place; the cap still fails a stuck fixer.
        if res["timed_out"]:
            base.save_hs(ctx, ticket, hs, "fixer_timeout",
                         "fixer run timed out; retrying in place, not re-entering review")
            return "fixer timed out (will retry)"
        git_ops.commit_all(ctx, ticket, hs, f"ticket #{ticket['id']}: address review findings")
        base.set_stage(ctx, ticket, "reviewing", hs, "fixed",
                       f"fixer addressed findings (round {hs.get('review_round', 0)})")
        return "fixed -> reviewing"

    # opening_pr -> merge_gate: the SHIPPER agent pushes the branch and opens the PR
    # (branch-level actions — reversible, and main is protected by the merge gate +
    # GitHub branch protection); the code then verifies the PR exists via gh -R and
    # records ONLY what GitHub reports, never what the agent claims.
    # Reality-checked + effect-marked for idempotency (a retried tick won't double-create).
    def _stage_opening_pr(self, ctx, ticket, hs):
        if hs.get("pr_number"):  # reality-check: PR already exists, don't double-create
            base.set_stage(ctx, ticket, "merge_gate", hs, "pr_reused",
                           f"PR #{hs['pr_number']} already open")
            return "pr already open"
        branch = hs.get("branch")
        if ctx.cfg.FAKE or not ticket["repo_path"]:
            num = 1000 + ticket["id"]
            hs["pr_number"], hs["pr_url"] = num, f"https://example.invalid/pr/{num}"
            base.set_stage(ctx, ticket, "merge_gate", hs, "pr_opened",
                           f"opened PR #{num} (reviewed & stable)",
                           detail={"pr_url": hs["pr_url"]})
            return f"opened PR #{num} -> merge gate"
        # Pre-flight: an unresolvable repo (repo_path not a URL / no origin remote) is
        # a deterministic config error — no shipper run or retry can fix it, and every
        # retry burns a full agent run. Fail loud BEFORE spawning the shipper; the
        # human fixes repo_path (UI edit) and hits Retry, which re-enters this stage.
        _, serr = git_ops.gh_slug(ctx, ticket)
        if serr:
            base.fail(ctx, ticket, f"cannot ship branch {branch}: {serr}")
            return "unshippable repo_path -> error"
        hs["pending_action"] = {"kind": "pr_create", "branch": branch}
        base.save_hs(ctx, ticket, hs, "effect_pending", "about to open PR")
        crit = (hs.get("groom") or {}).get("acceptance_criteria", [])
        res = agent.run_agent(
            ctx, "shipper", ticket_id=ticket["id"], ticket=ticket, cwd=hs.get("worktree_path"),
            worktree_path=hs.get("worktree_path"), allowed_tools="Bash",
            prompt=f"Ship the reviewed branch {branch} from this worktree:\n"
                   f"1. Push it: git push -u origin {branch}\n"
                   f"2. If no PR exists for it yet, open one (do NOT create a duplicate if"
                   f" one exists). Write a formatted markdown PR description first, then"
                   f" open the PR with it:\n"
                   f"   - Review the change: git diff {hs.get('base_branch') or 'origin/HEAD'}...{branch}\n"
                   f"   - Write the description to PR_BODY.md in this worktree with these"
                   f" sections: '## Summary' (what changed and why, 1-3 sentences),"
                   f" '## Changes' (bullet list of the notable changes), and '## Test plan'"
                   f" (how it was/should be verified). Base it on the ticket and the actual"
                   f" diff — do not invent changes that aren't in the diff.\n"
                   f"   - Open it: gh pr create --head {branch}"
                   f" --title <a concise title from the ticket> --body-file PR_BODY.md\n"
                   f"TICKET TITLE: {ticket['title']}\nTICKET BODY: {ticket['body']}\n"
                   f"ACCEPTANCE CRITERIA: {crit}\n"
                   f"Do NOT merge anything and do NOT touch any other branch.")
        if not base.note_agent(ctx, ticket, hs, res):
            return "failed: shipper timed out"
        num, url, err = git_ops.verify_pr(ctx, ticket, branch)
        if not num:
            # Never enter merge_gate with pr_number=None — every downstream gh call
            # would degrade confusingly. A PR-create failure is usually deterministic
            # (push rejected, auth), so cap retries here rather than burning a full
            # shipper run per attempt up to the global MAX_ATTEMPTS ceiling.
            tries = hs.get("pr_create_attempts", 0) + 1
            if tries >= ctx.cfg.MAX_PR_CREATE_ATTEMPTS:
                # Clear the counter BEFORE failing (mirrors _retry's attempts=0): a human
                # Retry re-enters this stage and must get a fresh 3-attempt budget, not
                # insta-fail on its first miss against a stale count.
                hs.pop("pr_create_attempts", None)
                base.save_hs(ctx, ticket, hs, "pr_create_gave_up",
                             f"clearing retry counter after {tries} attempts")
                base.fail(ctx, ticket,
                          f"PR create failed {tries}x for branch {branch}: {err or 'unknown'}")
                return "pr create failed (gave up)"
            hs["pr_create_attempts"] = tries
            base.save_hs(ctx, ticket, hs, "pr_create_failed",
                         f"no PR verifiable for branch {branch} (attempt {tries}):"
                         f" {err or 'unknown'}")
            return "pr create failed (will retry)"
        hs.pop("pr_create_attempts", None)
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
            hs.pop("pending_action", None)
            # A conflicted PR is workable, not fatal: GitHub refuses the merge until
            # the branch absorbs what landed on base since it forked. Route to an
            # agent that resolves the conflicts (never auto-take ours/theirs here).
            if git_ops.pr_mergeable(ctx, ticket, hs) == "CONFLICTING":
                base.set_stage(ctx, ticket, "resolving_conflicts", hs, "merge_conflicted",
                               f"PR #{hs.get('pr_number')} conflicts with"
                               f" {hs.get('base_branch') or 'the base branch'};"
                               " sending an agent to resolve and push")
                return "merge conflicted -> resolving"
            # A broken action is an error, not a go/no-go — fail it so the UI shows the
            # error (with a Retry that re-enters this stage), never approve/reject.
            base.fail(ctx, ticket, f"gh merge failed for PR #{hs.get('pr_number')}")
            return "merge command failed -> error"
        hs.pop("pending_action", None)
        git_ops.cleanup_worktree(ctx, ticket, hs)
        base.set_stage(ctx, ticket, "merged", hs, "merged",
                       f"merged PR #{hs.get('pr_number')} (squash); worktree cleaned up")
        return "merged"

    # resolving_conflicts -> merge_gate: a conflicted PR lands here after GitHub refused
    # a human-approved merge. "Agent does, code verifies": the AGENT owns the git work
    # (recreate the workspace if it was reaped, merge base, resolve by hand, push) and
    # the code only reads back GitHub's mergeability verdict — then RE-GATES the merge,
    # because the tree the human approved is not the tree that will land.
    def _stage_resolving_conflicts(self, ctx, ticket, hs):
        branch = hs.get("branch")
        base_branch = hs.get("base_branch") or "origin/main"
        wt = hs.get("worktree_path") or str(git_ops.worktree_path(ticket, branch))
        hs["worktree_path"] = wt
        config.ensure_dirs()
        clone = config.REPOS_DIR / git_ops.clone_name(ticket["repo_path"] or "unset")
        crit = (hs.get("groom") or {}).get("acceptance_criteria", [])
        res = agent.run_agent(
            ctx, "fixer", ticket_id=ticket["id"], ticket=ticket,
            cwd=config.WORKTREES_DIR, worktree_path=wt, allowed_tools="Edit,Write,Bash",
            prompt=f"PR #{hs.get('pr_number')} ({hs.get('pr_url')}) for branch {branch} has"
                   f" merge conflicts with {base_branch}, so GitHub refuses to merge it."
                   f" Resolve the conflicts.\n"
                   f"FIRST set up the workspace with the shell (it may have been cleaned up"
                   f" — the branch still exists on the remote):\n"
                   f"1. Ensure a clone of {ticket['repo_path']} exists at {clone}"
                   f" (clone it there if missing; reuse it if present), then fetch:"
                   f" git -C {clone} fetch origin\n"
                   f"2. Ensure a worktree for {branch} exists at {wt} (reuse it if present;"
                   f" if the local branch is gone, recreate it from origin/{branch}).\n"
                   f"THEN, inside {wt}:\n"
                   f"3. Run git merge {base_branch} and resolve EVERY conflict by"
                   f" understanding both sides — keep this ticket's change AND what landed"
                   f" on {base_branch} since the branch forked. Never blindly take"
                   f" ours/theirs.\n"
                   f"4. Verify the result still satisfies the ticket, commit the merge,"
                   f" and push the branch: git push origin {branch}\n"
                   f"Do NOT merge the PR itself and do NOT touch any other branch —"
                   f" merging is the orchestrator's job.\n"
                   f"TITLE: {ticket['title']}\nBODY: {ticket['body']}\n"
                   f"ACCEPTANCE: {crit}" + base.operator_notes(hs))
        if not base.note_agent(ctx, ticket, hs, res):
            return "failed: conflict resolution timed out"
        if res["timed_out"]:
            base.save_hs(ctx, ticket, hs, "resolver_timeout",
                         "conflict-resolver run timed out; retrying in place")
            return "resolver timed out (will retry)"
        # Verify, don't trust: only GitHub's mergeability verdict advances the ticket.
        # UNKNOWN (still recomputing after the push) advances too — the merging stage
        # re-checks and routes back here if the conflict is in fact still live.
        if git_ops.pr_mergeable(ctx, ticket, hs) == "CONFLICTING":
            tries = hs.get("resolve_attempts", 0) + 1
            if tries >= ctx.cfg.MAX_RESOLVE_ATTEMPTS:
                # Clear the counter BEFORE failing (mirrors pr_create_attempts): a human
                # Retry re-enters this stage and must get a fresh attempt budget.
                hs.pop("resolve_attempts", None)
                base.save_hs(ctx, ticket, hs, "resolve_gave_up",
                             f"clearing retry counter after {tries} attempts")
                base.fail(ctx, ticket, f"PR #{hs.get('pr_number')} still conflicting"
                                       f" after {tries} resolution attempts")
                return "still conflicting (gave up)"
            hs["resolve_attempts"] = tries
            base.save_hs(ctx, ticket, hs, "still_conflicting",
                         f"PR still conflicting after resolver attempt {tries}; retrying")
            return "still conflicting (will retry)"
        hs.pop("resolve_attempts", None)
        base.set_stage(ctx, ticket, "merge_gate", hs, "conflicts_resolved",
                       f"agent merged {base_branch} into {branch} and pushed;"
                       " re-gating the merge on the updated tree")
        return "conflicts resolved -> merge gate"

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
