# Self-contained: answering a merge gate with 'rework' (the third option next to
# approve/reject) sends the ticket back to fixing with the human's note threaded as
# findings — it neither merges nor kills the ticket. FAKE mode, throwaway DB, no deps.
import os, sys, atexit, shutil, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-rework-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import db
from outerloop.tick import run_tick
db.init_db()

c = db.connect()
c.execute("INSERT INTO ticket(title, body, type) VALUES"
          "('Add a cache layer', 'small change', 'coding')")
c.close()

NOTE = "Please also invalidate the cache on writes."


def answer(kind, action, note=""):
    """Mimic web._answer: rework rides on status='rejected' with rework=1."""
    c = db.connect()
    d = c.execute("SELECT * FROM decision WHERE status='pending' AND kind=?", (kind,)).fetchone()
    if not d:
        c.close(); return False
    status = "approved" if action == "approve" else "rejected"
    with db.immediate(c):
        c.execute("UPDATE decision SET status=?, rework=?, answer_note=?,"
                  " answered_at=datetime('now') WHERE id=?",
                  (status, 1 if action == "rework" else 0, note, d["id"]))
        c.execute("UPDATE ticket SET status='active' WHERE id=?", (d["ticket_id"],))
    c.close(); return True


reworked = False
went_to_fixing_after_rework = False
for i in range(40):
    run_tick()
    c = db.connect()
    t = c.execute("SELECT status, sub_stage, handler_state FROM ticket WHERE id=1").fetchone()
    c.close()
    if reworked and not went_to_fixing_after_rework and t["status"] != "blocked":
        hs = json.loads(t["handler_state"])
        assert t["sub_stage"] == "fixing", \
            f"rework on merge must route to fixing, got {t['sub_stage']}"
        assert any(NOTE in f for f in hs.get("last_findings", [])), \
            f"rework note not handed to the fixer: {hs.get('last_findings')}"
        assert {"q": "(operator note)", "a": NOTE} in hs.get("clarifications", []), \
            "rework note must be threaded as an operator note"
        went_to_fixing_after_rework = True
    if t["status"] == "blocked":
        if not reworked:
            reworked = answer("merge", "rework", NOTE)   # first merge gate: request changes
        else:
            answer("merge", "approve"); answer("deploy", "approve")
    if t["status"] == "done":
        break

assert reworked, "the merge gate never appeared"
assert went_to_fixing_after_rework, "the ticket never re-entered fixing after rework"

c = db.connect()
t = c.execute("SELECT status FROM ticket WHERE id=1").fetchone()
c.close()
assert t["status"] == "done", f"ticket did not finish after rework round: {t['status']}"
print("PASSED: rework sent the ticket back to fixing with the note, then finished on approve")
