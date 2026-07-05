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

"""Targeted tests for the safety machinery (skeptic must-fixes #1, #3, #6 + leases)."""
import json
from pathlib import Path
from outerloop import config, db, leasing, git_ops
from outerloop.tick import run_tick
from outerloop.handlers.coding import CodingHandler

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


# --- Test 1: heartbeat tick-lock (must-fix #1) ---------------------------------
c = fresh("t_lock")
c.execute("INSERT INTO tick_run(id,pid,boot_uuid,heartbeat_at) VALUES('other',1,'OTHERBOOT',datetime('now'))")
assert leasing.acquire_tick_lock(c, "mine") is False, "should refuse: a live tick is running"
c.execute("UPDATE tick_run SET heartbeat_at=datetime('now','-300 seconds') WHERE id='other'")
assert leasing.acquire_tick_lock(c, "mine2") is True, "should acquire: other tick's heartbeat is stale"
assert c.execute("SELECT status FROM tick_run WHERE id='other'").fetchone()["status"] == "crashed"
print("OK #1 tick-lock: live tick blocks a second; stale heartbeat is reaped")

# --- Test 2: per-ticket lease atomicity + expired-steal -------------------------
c = fresh("t_lease")
c.execute("INSERT INTO ticket(title,body,type) VALUES('x','','coding')")
assert leasing.acquire_lease(c, 1, "A") is True
assert leasing.acquire_lease(c, 1, "B") is False, "second claimant must fail"
leasing.release_lease(c, 1)
assert leasing.acquire_lease(c, 1, "B") is True, "claim after release"
c.execute("UPDATE lease SET expires_at=datetime('now','-1 minute') WHERE ticket_id=1")
assert leasing.acquire_lease(c, 1, "C") is True, "expired lease must be steal-able"
print("OK lease: one holder at a time; release frees it; expired lease is reclaimed")

# --- Test 3: green CI is a HARD merge precondition (must-fix #3) ----------------
c = fresh("t_ci")
orig_checks = git_ops.checks_green
git_ops.checks_green = lambda ctx, t, hs: (False, "required check failing")
try:
    hs = {"branch": "outerloop/ticket-1-abc", "pr_number": 1001,
          "pr_url": "https://example.invalid/pr/1001", "worktree_path": "/tmp/x"}
    c.execute("INSERT INTO ticket(title,body,type,status,sub_stage,handler_state,score)"
              " VALUES('m','','coding','active','merging',?,10)", (json.dumps(hs),))
    run_tick()
    row = c.execute("SELECT status, sub_stage FROM ticket WHERE id=1").fetchone()
    dec = c.execute("SELECT kind, json_extract(context,'$.checks_green') g FROM decision"
                    " WHERE ticket_id=1 AND status='pending'").fetchone()
    assert row["status"] == "blocked", f"red CI must block, got {row['status']}"
    assert dec and dec["kind"] == "merge" and dec["g"] == 0, "must re-gate merge with checks_green=false"
    # and it did NOT merge:
    merged = json.loads(c.execute("SELECT handler_state h FROM ticket WHERE id=1").fetchone()["h"]).get("merged")
    assert not merged, "must not have merged with red CI"
finally:
    git_ops.checks_green = orig_checks
print("OK #3 green-CI precondition: red checks re-gate the merge, no merge happens")

# --- Test 3b: approved no-CI merge proceeds (no infinite re-approve loop) --------
c = fresh("t_noci")
git_ops.checks_green = lambda ctx, t, hs: (False, git_ops.NO_CI_STATUS)
try:
    hs = {"branch": "outerloop/ticket-1-abc", "pr_number": 1002,
          "pr_url": "https://example.invalid/pr/1002", "worktree_path": "/tmp/x"}
    c.execute("INSERT INTO ticket(title,body,type,status,sub_stage,handler_state,score)"
              " VALUES('m','','coding','active','merging',?,10)", (json.dumps(hs),))
    run_tick()
    row = c.execute("SELECT status, sub_stage FROM ticket WHERE id=1").fetchone()
    assert row["sub_stage"] == "merged", f"approved no-CI merge must proceed, got {row['sub_stage']}"
    dec = c.execute("SELECT 1 FROM decision WHERE ticket_id=1 AND status='pending'").fetchone()
    assert not dec, "must NOT re-gate when the only issue is no CI (already approved)"
finally:
    git_ops.checks_green = orig_checks
print("OK #3b no-CI approval: an approved merge with no CI merges instead of re-asking forever")

# --- Test 4: stall guard fails a stage that errors every tick (must-fix #6) -----
c = fresh("t_stall")
config.MAX_ATTEMPTS = 3
orig_stage = CodingHandler._stage_groomed
def boom(self, ctx, ticket, hs):
    raise RuntimeError("simulated persistent stage failure")
CodingHandler._stage_groomed = boom
try:
    c.execute("INSERT INTO ticket(title,body,type) VALUES('stall me','body','coding')")
    final = None
    for _ in range(10):
        run_tick()
        final = c.execute("SELECT status FROM ticket WHERE id=1").fetchone()["status"]
        if final == "failed":
            break
    assert final == "failed", f"a perpetually-failing stage must end 'failed', got {final}"
    errs = c.execute("SELECT COUNT(*) n FROM audit WHERE ticket_id=1 AND action='error'").fetchone()["n"]
    assert errs >= 1, "the exception should have been audited, not swallowed silently"
finally:
    CodingHandler._stage_groomed = orig_stage
    config.MAX_ATTEMPTS = 12
print("OK #6 stall guard: a stage that errors every tick is failed (not an infinite runaway)")

print("\n=== SAFETY TESTS PASSED ===")
