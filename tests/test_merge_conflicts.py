# Self-contained: runs from anywhere, uses a throwaway FAKE-mode DB. No env setup needed.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-test-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
from outerloop import db as _bootstrap_db
_bootstrap_db.init_db()
# --- test body ---

"""A merge GitHub refuses because the PR conflicts with base must route to the
resolving_conflicts agent stage (never fail the ticket, never auto-resolve in code),
and only GitHub's mergeability verdict may advance it back to the merge gate."""
import json
from pathlib import Path
from outerloop import config, db, git_ops
from outerloop.tick import run_tick

BASE = Path(config.HOME)


def fresh(name):
    config.DB_PATH = BASE / f"{name}.db"
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    for suf in ("-wal", "-shm"):
        p = Path(str(config.DB_PATH) + suf)
        if p.exists():
            p.unlink()
    return db.init_db()


HS = {"branch": "outerloop/ticket-1-abc", "pr_number": 1001, "base_branch": "origin/main",
      "pr_url": "https://example.invalid/pr/1001", "worktree_path": "/tmp/x",
      "clarifications": [{"q": "(operator note)", "a": "resolve conflicts and try again"}]}


def seed_merging(c):
    c.execute("INSERT INTO ticket(title,body,type,status,sub_stage,handler_state,score)"
              " VALUES('m','','coding','active','merging',?,10)", (json.dumps(HS),))


# --- Test 1: conflicted merge -> resolving_conflicts, not failed -----------------
c = fresh("t_conflict")
orig_merge, orig_mergeable = git_ops.merge_pr, git_ops.pr_mergeable
git_ops.merge_pr = lambda ctx, t, hs: False
git_ops.pr_mergeable = lambda ctx, t, hs: "CONFLICTING"
try:
    seed_merging(c)
    run_tick()
    row = c.execute("SELECT status, sub_stage, handler_state FROM ticket WHERE id=1").fetchone()
    assert row["status"] == "active", f"conflict must stay workable, got {row['status']}"
    assert row["sub_stage"] == "resolving_conflicts", \
        f"conflicted merge must route to the resolver, got {row['sub_stage']}"
    assert "pending_action" not in json.loads(row["handler_state"]), \
        "the merge effect-marker must be cleared when detouring to the resolver"
finally:
    git_ops.merge_pr, git_ops.pr_mergeable = orig_merge, orig_mergeable
print("OK #1 conflicted merge routes to resolving_conflicts instead of failing")

# --- Test 2: non-conflict merge failure still fails loud -------------------------
c = fresh("t_hardfail")
git_ops.merge_pr = lambda ctx, t, hs: False
git_ops.pr_mergeable = lambda ctx, t, hs: "MERGEABLE"  # e.g. auth/network broke, not a conflict
try:
    seed_merging(c)
    run_tick()
    row = c.execute("SELECT status FROM ticket WHERE id=1").fetchone()
    assert row["status"] == "failed", f"a non-conflict merge error must fail, got {row['status']}"
finally:
    git_ops.merge_pr, git_ops.pr_mergeable = orig_merge, orig_mergeable
print("OK #2 non-conflict merge failure still fails the ticket")

# --- Test 3: resolver run + now-mergeable PR -> back to the merge gate -----------
c = fresh("t_resolved")
git_ops.pr_mergeable = lambda ctx, t, hs: "MERGEABLE"
try:
    hs = dict(HS)
    c.execute("INSERT INTO ticket(title,body,type,status,sub_stage,handler_state,score)"
              " VALUES('m','','coding','active','resolving_conflicts',?,10)", (json.dumps(hs),))
    run_tick()
    row = c.execute("SELECT status, sub_stage FROM ticket WHERE id=1").fetchone()
    assert row["sub_stage"] == "merge_gate", \
        f"a resolved PR must be RE-GATED (tree changed since approval), got {row['sub_stage']}"
    ran = c.execute("SELECT role FROM agent_run WHERE ticket_id=1").fetchall()
    assert any(r["role"] == "fixer" for r in ran), "an agent run must do the resolution"
    prompt = c.execute("SELECT prompt FROM agent_run WHERE ticket_id=1").fetchone()["prompt"]
    assert "resolve conflicts and try again" in prompt, \
        "operator notes must be threaded into the resolver prompt"
finally:
    git_ops.pr_mergeable = orig_mergeable
print("OK #3 resolver run + mergeable verdict re-enters the merge gate")

# --- Test 4: still-conflicting PR retries, then fails at the cap ----------------
c = fresh("t_unresolved")
git_ops.pr_mergeable = lambda ctx, t, hs: "CONFLICTING"
try:
    c.execute("INSERT INTO ticket(title,body,type,status,sub_stage,handler_state,score)"
              " VALUES('m','','coding','active','resolving_conflicts',?,10)", (json.dumps(HS),))
    final = None
    for _ in range(config.MAX_RESOLVE_ATTEMPTS + 2):
        run_tick()
        row = c.execute("SELECT status, sub_stage FROM ticket WHERE id=1").fetchone()
        final = row["status"]
        if final == "failed":
            break
        assert row["sub_stage"] == "resolving_conflicts", "must retry in place until the cap"
    assert final == "failed", f"a never-resolving conflict must end 'failed', got {final}"
    hs = json.loads(c.execute("SELECT handler_state FROM ticket WHERE id=1").fetchone()["handler_state"])
    assert "resolve_attempts" not in hs, "counter must be cleared so a human Retry gets a fresh budget"
finally:
    git_ops.pr_mergeable = orig_mergeable
print("OK #4 still-conflicting PR retries then fails at the cap with a fresh-budget counter")
