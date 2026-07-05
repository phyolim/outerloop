# Self-contained: the decisions surface (the /ui/* JSON the SPA renders) shows errors
# as errors (no approve/reject), whitelists decision context (PR link + diff, never
# internal payloads), and Retry/Dismiss move a failed ticket out of the queue.
# FAKE mode, throwaway DB, no deps.
import os, sys, atexit, shutil, tempfile, threading, time, json
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-dec-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import db
from outerloop.web import Handler

db.init_db()
c = db.connect()

# A failed coding ticket (the "error doing the task" case) + a clean pending merge decision.
# The decision context carries an internal field (drafted_body) that must NOT reach the UI.
c.execute("INSERT INTO ticket(id,title,body,type,status,handler_state) VALUES"
          "(1,'Broken task','x','coding','failed',?)", (json.dumps({"pr_url": "http://pr/1"}),))
db.append_audit(c, "handler:coding", "failed", "gh merge failed for PR #7", ticket_id=1)
c.execute("INSERT INTO ticket(id,title,body,type,status) VALUES"
          "(2,'Good task','y','coding','blocked')")
c.execute("INSERT INTO decision(id,ticket_id,kind,question,context,status) VALUES"
          "(1,2,'merge','Merge PR #9?',?, 'pending')",
          (json.dumps({"pr_url": "http://pr/9", "diff_stat": "3 files +40-2",
                       "checks": "green", "checks_green": True,
                       "drafted_body": "INTERNAL DRAFT — never show this"}),))
c.close()

PORT = 8811
BASE = f"http://127.0.0.1:{PORT}"
srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)


def get_json(path):
    return json.loads(urlopen(BASE + path).read())


def post_json(path, obj):
    req = Request(BASE + path, data=json.dumps(obj).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urlopen(req).read())


queue = {t["id"]: t for t in get_json("/ui/decisions.json")["tickets"]}

# The failed ticket surfaces as an ERROR (its failure reason as the preview), not as
# something approvable; the clean decision surfaces as an approvable merge.
assert queue[1]["reason"] == "error", queue[1]
assert "gh merge failed for PR #7" in queue[1]["preview"], "error reason must be shown"
assert queue[2]["reason"] == "merge", queue[2]

# The ticket thread offers nothing to approve on an error.
t1 = get_json("/ui/ticket.json?id=1")
assert t1["failed"] is True and t1["pending"] is None, "an error must not be approvable"

# The clean decision's context is a whitelisted summary — PR + diff + checks — and the
# internal drafted payload never reaches the UI.
t2 = get_json("/ui/ticket.json?id=2")
ctx = t2["pending"]["context"]
assert ctx["pr_url"] == "http://pr/9" and ctx["diff_stat"] == "3 files +40-2"
assert ctx["checks_green"] is True
assert "drafted_body" not in ctx, "context must be whitelisted, not dumped wholesale"

# Retry sends the failed ticket back to active at its stage, resetting the stall counter.
post_json("/ui/retry", {"ticket_id": 1})
t = db.connect().execute("SELECT status, attempts FROM ticket WHERE id=1").fetchone()
assert t["status"] == "active" and t["attempts"] == 0, f"retry should re-activate, got {dict(t)}"
ids = {x["id"] for x in get_json("/ui/decisions.json")["tickets"]}
assert 1 not in ids, "retried ticket must leave the error queue"

# Dismiss closes a failed ticket (set it back to failed first, then dismiss).
c = db.connect(); c.execute("UPDATE ticket SET status='failed' WHERE id=1"); c.close()
post_json("/ui/dismiss", {"ticket_id": 1})
assert db.connect().execute("SELECT status FROM ticket WHERE id=1").fetchone()["status"] == "done"

srv.shutdown()
print("PASSED: errors shown as errors, context whitelisted, retry/dismiss work")
