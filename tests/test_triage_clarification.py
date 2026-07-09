# Self-contained: a real-but-too-vague ticket is NOT parked as junk — triage blocks it on
# a 'clarification' decision (asks the operator for detail), and once answered the ticket
# resumes through its handler to completion, the answer threaded back. FAKE mode, throwaway DB.
import os, sys, atexit, shutil, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-triage-clar-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import db
from outerloop.tick import run_tick
db.init_db()

c = db.connect()
# Title < 4 chars, no body -> "too thin to act on": a real ask missing detail, not junk.
c.execute("INSERT INTO ticket(title, body, type) VALUES ('Do', '', 'coding')")
c.close()


def answer(kind, note):
    c = db.connect()
    d = c.execute("SELECT * FROM decision WHERE status='pending' AND kind=?", (kind,)).fetchone()
    if not d:
        c.close(); return False
    with db.immediate(c):
        c.execute("UPDATE decision SET status='approved', answer_note=?, answered_at=datetime('now')"
                  " WHERE id=?", (note, d["id"]))
        c.execute("UPDATE ticket SET status='active' WHERE id=?", (d["ticket_id"],))
    c.close(); return True


saw_clarify = False
answered = False
for _ in range(30):
    run_tick()
    c = db.connect()
    t = c.execute("SELECT status, sub_stage FROM ticket WHERE id=1").fetchone()
    d = c.execute("SELECT kind, context FROM decision WHERE ticket_id=1 AND status='pending'").fetchone()
    c.close()
    assert t["status"] != "parked", "a vague-but-real ticket must be clarified, not parked as junk"
    if d and d["kind"] == "clarification":
        saw_clarify = True
        assert t["status"] == "blocked", "a clarification must BLOCK the ticket, not run it"
        assert json.loads(d["context"]).get("asked_by") == "triage", \
            "the triage clarification must record who asked"
        answered = answer("clarification", "Rename deploy.sh and update the README.")
    else:
        answer("merge", ""); answer("deploy", "")  # auto-approve human gates so it can finish
    if t["status"] == "done":
        break

assert saw_clarify, "triage never asked for clarification on the vague ticket (it parked or ran it)"
assert answered, "the clarification was never answered"

c = db.connect()
t = c.execute("SELECT status, handler_state FROM ticket WHERE id=1").fetchone()
hs = json.loads(t["handler_state"])
c.close()

assert t["status"] == "done", f"ticket did not finish after clarification: {t['status']}"
assert any(cl.get("a") == "Rename deploy.sh and update the README."
           for cl in hs.get("clarifications", [])), \
    f"the operator's answer was not threaded into handler_state: {hs.get('clarifications')}"
print("PASSED: vague ticket clarified (not parked), resumed with the answer, finished")
