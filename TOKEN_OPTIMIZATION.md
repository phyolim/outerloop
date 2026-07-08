# Token-usage optimization plan

> Status (2026-07-08): items 1–8 implemented and merged. Item 9 (session resume)
> is the remaining follow-up — moderate risk, do deliberately with the fallback.

Scope: reduce agent-token spend per ticket. Only changes with a very low chance of
breaking the lifecycle. Ordered by (expected savings × confidence) / risk — do them
in order. Verify after each item: `python3 -m pytest tests/` plus one FAKE-mode
ticket walked end to end (`OUTERLOOP_FAKE=1`).

Baseline per happy-path coding ticket: triage(haiku) + scorer(haiku) +
groomer(sonnet) + author(opus) + reviewer(sonnet) + shipper(haiku); each review
round adds fixer(opus) + reviewer(sonnet, full diff re-sent).

---

## 1. Merge triage + scorer into one haiku call

Two agent calls per ticket with the identical input (`TITLE + BODY`):
- triage: `outerloop/triage.py:37-44`
- scorer: `outerloop/scoring.py:23-31`

Change: extend the triage role's schema (`outerloop/agent.py:61` ROLE_SCHEMAS) to
also return the four scoring factors + reversibility + justification. In
`triage_new`, when a ticket is accepted, store the factors and score in the same
transaction. `score_unscored` keeps working unchanged as a backfill for tickets
that somehow lack a score (don't delete it).

Keep the `_fake` triage response in sync (add factors) so FAKE mode still walks.

Savings: one haiku call per ticket, and one fewer serial agent call blocking the
hub scheduler thread.
Risk: very low — both consumers already tolerate missing/garbled fields
(`scoring.factor()` defaults to 3; `keep` defaults to True).

## 2. Feed the fixer real context (cuts opus review rounds)

`outerloop/handlers/coding.py:210-217` — the fixer prompt contains ONLY the
findings list. No ticket title/body, no acceptance criteria. It re-derives intent
from the worktree at opus prices, and a misread finding costs a full extra round
(opus fixer + sonnet reviewer with full diff).

Change: add to the fixer prompt: `TITLE`, `BODY`, `ACCEPTANCE` (same fields the
author gets, coding.py:113-116). Do NOT add the diff (the fixer has the worktree).

Savings: fewer review↔fix rounds — each avoided round is an opus run + a sonnet
run with the full diff. This is the biggest lever on the expensive loop.
Risk: very low — prompt-only, adds ~a few hundred input tokens.

## 3. Skip the reviewer when the branch diff is empty

`outerloop/handlers/coding.py:177-184` — if `git_ops.branch_diff` returns empty
(e.g. the `origin/HEAD` fallback at `outerloop/git_ops.py:149-151` degrades the
base to `HEAD`, making the diff `HEAD...HEAD`), the reviewer is paid to review
nothing and may approve, shipping unreviewed code.

Change: in `_stage_reviewing`, if the diff is empty/whitespace, do NOT call the
reviewer. Fail the ticket with a clear reason ("branch diff is empty — nothing to
review; check base_branch"). FAKE mode returns a canned non-empty diff, unaffected.

Savings: a wasted sonnet call on a broken path; also closes a review-bypass hole.
Risk: very low — only fires when the diff is empty, which is never a healthy state.

## 4. Cap PR-create retries

`outerloop/handlers/coding.py:263-271` — a failed `pr create` leaves state
unchanged so the attempts ceiling retries it: up to MAX_ATTEMPTS=12 full shipper
agent runs against a failure that is usually deterministic (push rejected, auth).

Change: count attempts in handler_state (`hs["pr_create_attempts"]`), fail the
ticket after 3 with the last error in the reason. Keep the existing audit line.

Savings: up to ~9 shipper runs + verify calls on the failure path.
Risk: very low — failure path only; 3 retries still covers transient network flakes.

## 5. Don't advance a timed-out author run into review

`outerloop/handlers/coding.py:119-166` — on an author timeout, `res["data"]` is
`{}`, so the stage falls through to `commit_all` + `verify_worktree`. If the
worktree exists, half-finished work is committed and marked `implemented`; the
review↔fix loop then finishes the implementation at reviewer+fixer prices.

Change: in `_stage_groomed`, after `note_agent` passes, if `res["timed_out"]` is
true, return without a stage change (e.g. `return "author timed out (will retry)"`).
The existing machinery already handles the rest: attempts ceiling counts it,
MAX_CONSEC_TIMEOUTS fails after 2 in a row, and the retry's setup prompt already
says "reuse the worktree if it exists" so the next author run continues in place
instead of starting over. Apply the same guard to `_stage_fixing` (coding.py:218-220:
a timed-out fixer currently commits whatever is lying around and bounces straight
back to the reviewer).

Savings: avoids reviewer+fixer rounds spent completing known-incomplete code.
Risk: low — changes only the timeout path; both ceilings still bound retries.

## 6. Pass the groomer's `tasks` to the author

`outerloop/handlers/coding.py:40-48` produces `tasks` + `acceptance_criteria`;
the author prompt (coding.py:113-116) only receives `acceptance_criteria`. The
task breakdown is paid for and never used.

Change: add `TASKS: {tasks}` next to `ACCEPTANCE:` in the author prompt.

Savings: indirect — a clearer author brief means fewer clarification gates (each
clarification currently costs a full fresh opus author run on resume) and fewer
review rounds. Costs ~tens of input tokens.
Risk: very low — prompt-only.

## 7. Config-only trial: fixer opus → sonnet

`outerloop/config.py:139-150` — the fixer does scoped work (address enumerated
findings in an existing worktree), which sonnet handles well. After item 2 lands
(fixer gets full context), change `ROLE_MODEL_DEFAULTS["fixer"]` to `"sonnet"`.

Savings: every review round drops from opus+sonnet to sonnet+sonnet (~5x cheaper
on the fixer side).
Risk: low and fully reversible — it's one line / an `OUTERLOOP_MODELS=fixer=opus`
env override to roll back. Watch review-round counts for a week; if rounds climb,
revert. Do NOT downgrade author (deep work) or groomer (its acceptance criteria
steer everything downstream).

## 8. Guard against pathological reviewer diffs

`outerloop/handlers/coding.py:179-184` inlines the full branch diff into the
reviewer prompt with no size bound — a lockfile churn or vendored-deps commit can
blow a single review round to hundreds of thousands of tokens, ×3 rounds.

Change: if the diff exceeds a cap (suggest 100_000 chars), do NOT truncate-and-
review (a blind approve is worse than spend). Instead `gate.require` a
`review_exhausted`-style decision: "diff too large for automated review
(<diff_stat>) — open PR for manual review, or reject?" (resume_stage
`opening_pr`, same shape as the existing review-exhausted gate at
coding.py:194-199).

Savings: caps the worst-case round; normal tickets unaffected.
Risk: very low — rare path, and it degrades to a human decision, never to a
blind approval.

---

## Phase 2 — biggest saver, needs a fallback (do last, more care)

## 9. Resume sessions instead of cold-starting same-identity reruns

Two loops re-run a full opus session that a `claude --resume <session_id>` turn
would make incremental:

- Clarification resume: `coding.py:143-147` blocks at `resume_stage="groomed"`;
  the resumed tick re-runs the ENTIRE author stage cold (re-read repo, redo work),
  up to MAX_CLARIFICATIONS=3 times. `hs["author_session_id"]` is already stored.
- Fixer rounds: the fixer may reuse the author identity by design (docstring,
  coding.py:207) but always starts cold.

Change: in `agent._real` (outerloop/agent.py:143-161), support a `resume`
session id (`claude --resume <id> -p ...` instead of `--session-id`). Use it for
(a) the author re-run when `hs["clarifications"]` is non-empty and
`hs["author_session_id"]` exists, and (b) the fixer when it does. MUST fall back
to a fresh session if the resume errors (session file missing — e.g. the ticket
is pinned to the right box, but a cleanup or reinstall removed it): detect a
nonzero fast exit with no result envelope and rerun fresh once.

Constraints that make this safe: fixing/reviewing stages are already pinned to
the box holding the worktree (`pin=ticket["assigned_worker"]`, coding.py:162-165),
so the session file is on the same machine. NEVER resume for the reviewer —
author≠reviewer separation is structural via fresh session ids (agent.py:2-4).

Savings: the largest single reduction — each clarification round and each fix
round stops paying for a full repo re-read at opus prices.
Risk: moderate (new CLI flag path, session-file lifetime) — hence last, and only
with the fresh-session fallback in place.

---

## Explicitly NOT doing (considered, rejected)

- Shipper agent → direct subprocess: deliberately agent-run per
  outerloop/git_ops.py:3-8 (env-adaptation history); haiku + short, small savings.
- Groomer sonnet → haiku: acceptance-criteria quality steers author, reviewer,
  and the round count — a downgrade can cost more than it saves.
- Truncating the reviewer diff: an approve on a partial diff is a review bypass;
  item 8 gates to a human instead.
- Reviewer diff "delta since last round": the reviewer must see the whole branch
  to approve it; sending only the delta changes review semantics.
