# Self-contained: a coding ticket whose author needs input pauses on a 'clarification'
# block, and resumes to completion once the human answers — the answer threaded back so
# it isn't re-asked. FAKE mode, throwaway DB, no deps.
import os, sys, atexit, shutil, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-clar-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import db
from outerloop.tick import run_tick
db.init_db()

c = db.connect()
# Body contains CLARIFY -> the FAKE author asks exactly once before proceeding.
c.execute("INSERT INTO ticket(title, body, type) VALUES"
          "('Add a cache layer', 'CLARIFY which datastore to use', 'coding')")
c.close()


def answer(kind, note):
    """Mimic the web /answer handler: record the answer + approve, unblock the ticket."""
    c = db.connect()
    d = c.execute("SELECT * FROM decision WHERE status='pending' AND kind=?", (kind,)).fetchone()
    if not d:
        c.close(); return False
    with db.immediate(c):
        c.execute("UPDATE decision SET status='approved', answer_note=?, answered_at=datetime('now')"
                  " WHERE id=?", (note, d["id"]))
        c.execute("UPDATE ticket SET status='active' WHERE id=?", (d["ticket_id"],))
    c.close(); return True


saw_clarification = False
answered = False
for i in range(30):
    run_tick()
    c = db.connect()
    d = c.execute("SELECT kind, context FROM decision WHERE ticket_id=1 AND status='pending'").fetchone()
    t = c.execute("SELECT status, sub_stage FROM ticket WHERE id=1").fetchone()
    c.close()
    if d and d["kind"] == "clarification":
        saw_clarification = True
        assert t["status"] == "blocked", "a clarification must BLOCK the ticket, not run it"
        # Multiple-choice question: options ride in the decision context so the UI can
        # render clickable picks (web._ctx_public whitelists them for the SPA).
        assert json.loads(d["context"]).get("options") == ["Postgres", "SQLite"], \
            f"author's multiple-choice options not stored on the decision: {d['context']}"
        answered = answer("clarification", "Use Postgres.")
    else:
        answer("merge", ""); answer("deploy", "")   # auto-approve the human gates to finish
    if t["status"] == "done":
        break

assert saw_clarification, "the author never raised a clarification block"
assert answered, "the clarification was never answered"

c = db.connect()
t = c.execute("SELECT status, sub_stage, handler_state FROM ticket WHERE id=1").fetchone()
hs = json.loads(t["handler_state"])
n_clar = c.execute("SELECT COUNT(*) n FROM decision WHERE ticket_id=1 AND kind='clarification'").fetchone()["n"]
c.close()

assert t["status"] == "done", f"ticket did not finish after clarification: {tuple(t)[:2]}"
assert hs.get("clarifications") == [{"q": hs["clarifications"][0]["q"], "a": "Use Postgres."}], \
    f"answer not threaded into handler_state: {hs.get('clarifications')}"
assert n_clar == 1, f"author re-asked after being answered ({n_clar} clarifications)"
print("PASSED: worker paused for clarification, resumed with the answer, finished without re-asking")
