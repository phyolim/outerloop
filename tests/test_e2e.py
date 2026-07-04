# Self-contained: runs from anywhere, uses a throwaway FAKE-mode DB. No env setup needed.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("INBOX_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-test-")
os.environ["INBOX_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
from inbox import db as _bootstrap_db
_bootstrap_db.init_db()
# --- test body ---

"""End-to-end FAKE-mode drive of the whole loop. Adds tickets, runs ticks, and
auto-approves decisions as a human would, then asserts the invariants."""
import json
from inbox import db
from inbox.tick import run_tick


def add(title, body, type_, repo=None):
    c = db.connect()
    c.execute("INSERT INTO ticket(title, body, type, repo_path) VALUES(?,?,?,?)",
              (title, body, type_, repo))
    c.close()


def answer_pending(decision_kinds, action="approve"):
    """Mimic the web /answer handler for any pending decision of the given kinds."""
    c = db.connect()
    rows = c.execute("SELECT * FROM decision WHERE status='pending'").fetchall()
    answered = []
    for d in rows:
        if d["kind"] not in decision_kinds:
            continue
        status = "approved" if action == "approve" else "rejected"
        with db.immediate(c):
            c.execute("UPDATE decision SET status=?, answered_at=datetime('now') WHERE id=?",
                      (status, d["id"]))
            c.execute("UPDATE ticket SET status='active' WHERE id=?", (d["ticket_id"],))
        answered.append((d["ticket_id"], d["kind"]))
    c.close()
    return answered


def snapshot():
    c = db.connect()
    rows = c.execute("SELECT id, type, status, sub_stage FROM ticket ORDER BY id").fetchall()
    pend = c.execute("SELECT ticket_id, kind FROM decision WHERE status='pending'").fetchall()
    c.close()
    s = "  ".join(f"#{r['id']}{r['type'][:1]}:{r['status']}/{r['sub_stage']}" for r in rows)
    p = ", ".join(f"#{r['ticket_id']}:{r['kind']}" for r in pend) or "-"
    return s, p


add("Add retry to the HTTP client", "wrap requests with exponential backoff", "coding")
add("Research vector DB options", "compare pgvector vs qdrant for our scale", "knowledge")
add("Reply to the venue about the offsite", "confirm dates and headcount", "ops")
add("test junk asdf", "", "coding")  # should be parked at triage

print("=== driving the loop (FAKE mode) ===")
for i in range(1, 22):
    run_tick()
    auto = answer_pending({"merge", "deploy", "irreversible_action"})
    s, p = snapshot()
    extra = f"   (auto-approved {auto})" if auto else ""
    print(f"tick {i:2}: {s}   pending=[{p}]{extra}")
    c = db.connect()
    alive = c.execute("SELECT COUNT(*) n FROM ticket WHERE status IN ('inbox','active','blocked')").fetchone()["n"]
    c.close()
    if alive == 0:
        print("all tickets terminal.")
        break

print("\n=== assertions ===")
c = db.connect()
def status(tid):
    return c.execute("SELECT status, sub_stage FROM ticket WHERE id=?", (tid,)).fetchone()

cod = status(1); kno = status(2); ops = status(3); junk = status(4)
assert cod["status"] == "done" and cod["sub_stage"] == "done", f"coding not done: {tuple(cod)}"
assert kno["status"] == "done", f"knowledge not done: {tuple(kno)}"
assert ops["status"] == "done", f"ops not done: {tuple(ops)}"
assert junk["status"] == "parked", f"junk not parked: {tuple(junk)}"
print("OK ticket terminal states: coding=done knowledge=done ops=done junk=parked")

# author != reviewer for the coding ticket (the structural invariant)
runs = c.execute("SELECT role, session_id FROM agent_run WHERE ticket_id=1").fetchall()
authors = {r["session_id"] for r in runs if r["role"] == "author"}
reviewers = {r["session_id"] for r in runs if r["role"] == "reviewer"}
assert authors and reviewers and not (authors & reviewers), f"author/reviewer overlap: {authors} {reviewers}"
print(f"OK author session(s) {authors} disjoint from reviewer session(s) {reviewers}")

# the review<->fix loop actually ran (round 0 request_changes -> round 1 approve)
n_rev = sum(1 for r in runs if r["role"] == "reviewer")
n_fix = sum(1 for r in runs if r["role"] == "fixer")
assert n_rev >= 2 and n_fix >= 1, f"loop did not exercise: reviewers={n_rev} fixers={n_fix}"
print(f"OK review/fix loop exercised: {n_rev} reviews, {n_fix} fix(es)")

# every coding ticket reached 'merged' only via an approved merge decision
merge_dec = c.execute("SELECT status FROM decision WHERE ticket_id=1 AND kind='merge'").fetchall()
assert merge_dec and all(d["status"] == "approved" for d in merge_dec), "merge not via approved decision"
print(f"OK merge happened only through {len(merge_dec)} approved merge decision(s)")

# audit log is append-only (the trigger fires)
try:
    c.execute("UPDATE audit SET reason='tampered' WHERE id=1")
    raise SystemExit("FAIL: audit was mutable!")
except Exception as e:
    assert "append-only" in str(e), e
print("OK audit log is append-only (UPDATE blocked by trigger)")

n_audit = c.execute("SELECT COUNT(*) n FROM audit").fetchone()["n"]
print(f"\naudit rows written: {n_audit}")
print("=== ALL ASSERTIONS PASSED ===")
c.close()
