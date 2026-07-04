# Self-contained: the author ignores the JSON contract and replies in PROSE that reads
# as a question (no {"question"} field). It must be routed to a 'clarification' decision,
# never silently committed as 'implemented'. FAKE mode, throwaway DB, no deps.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("INBOX_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-prose-")
os.environ["INBOX_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from inbox import db, agent
db.init_db()

PROSE_Q = "Which datastore should this target - Postgres or SQLite?"
_orig_fake = agent._fake
def _fake(role, prompt):
    # An author that replies in prose yields a bare {"text": ...} (parse never found the
    # {"summary"...}|{"question"...} shape) — exactly the gap we are closing.
    if role == "author":
        return {"text": PROSE_Q}
    return _orig_fake(role, prompt)
agent._fake = _fake

from inbox.tick import run_tick

c = db.connect()
c.execute("INSERT INTO ticket(title, body, type) VALUES('Add a cache','build it','coding')")
c.close()

raised = None
for _ in range(10):
    run_tick()
    c = db.connect()
    d = c.execute("SELECT kind, question FROM decision WHERE ticket_id=1 AND status='pending'").fetchone()
    t = c.execute("SELECT status FROM ticket WHERE id=1").fetchone()
    c.close()
    if d and d["kind"] == "clarification":
        raised = d
        assert t["status"] == "blocked", "a prose question must BLOCK the ticket, not run it"
        break

assert raised, "author prose question was not routed to a clarification (silently committed?)"
assert PROSE_Q in raised["question"], f"clarification did not carry the prose text: {raised['question']!r}"
c = db.connect()
n_impl = c.execute("SELECT COUNT(*) n FROM audit WHERE ticket_id=1 AND to_stage='implemented'").fetchone()["n"]
c.close()
assert n_impl == 0, "ticket was marked 'implemented' despite an unanswered prose question"
print("PASSED: author prose question routed to a clarification, not silently committed")
