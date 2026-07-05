# Self-contained: hub-wide spend ceiling (#2). FAKE mode, throwaway DB.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-budget-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
from outerloop import db as _bdb
_bdb.init_db()
# --- test body ---
from outerloop import db, claim

c = db.connect()
c.execute("INSERT INTO worker(name, capabilities, status, last_seen)"
          " VALUES('air', '[]', 'online', datetime('now'))")
c.execute("INSERT INTO ticket(title, body, type, status, requires, score)"
          " VALUES('a note', '', 'knowledge', 'active', '[]', 10)")
tid = c.execute("SELECT id FROM ticket WHERE title='a note'").fetchone()["id"]
# record agent token usage that exceeds a low cap, within the window
c.execute("INSERT INTO agent_run(id, ticket_id, role, tick_id, session_id, prompt,"
          " tokens_in, tokens_out) VALUES('r1', ?, 'knowledge', 'x', 'r1', 'p',"
          " 1500, 500)", (tid,))
db.set_setting(c, "fleet_budget_tokens", "1000")

assert claim.over_budget(c) is True, "2000 tokens should exceed cap 1000"
assert claim.claim(c, "air") is None, "over budget: claims must be refused fleet-wide"
assert c.execute("SELECT COUNT(*) n FROM lease").fetchone()["n"] == 0, "no lease taken while halted"
print("OK over budget -> claims refused, no work leased")

db.set_setting(c, "fleet_budget_tokens", "1000000")
res = claim.claim(c, "air")
assert res and res["ticket"]["id"] == tid, "under budget: the ticket should claim"
print("OK budget raised -> claim resumes")
print("\n=== BUDGET TEST PASSED ===")
c.close()
