# Self-contained: the Close button ends a no-longer-relevant ticket from any state —
# it fences the in-flight worker (epoch bump), drops the lease, and voids the pending
# decision so the Approvals queue clears. FAKE mode, throwaway DB, no deps.
import os, sys, atexit, shutil, tempfile, threading, time, json
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("INBOX_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-close-")
os.environ["INBOX_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from inbox import db
from inbox.web import Handler

db.init_db()
c = db.connect()
# A blocked ticket with a pending decision AND a live lease (worker mid-flight).
c.execute("INSERT INTO ticket(id,title,body,type,status,claim_epoch) VALUES"
          "(1,'Stale idea','x','coding','blocked',3)")
c.execute("INSERT INTO decision(id,ticket_id,kind,question,status) VALUES"
          "(1,1,'merge','Merge?','pending')")
c.execute("UPDATE ticket SET blocked_by_decision_id=1 WHERE id=1")
c.execute("INSERT INTO lease(ticket_id,owner,pid,boot_uuid,expires_at) VALUES"
          "(1,'tick-x',1,'boot',datetime('now','+10 minutes'))")
c.close()

PORT = 8813
BASE = f"http://127.0.0.1:{PORT}"
srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)


def post_json(path, payload):
    req = Request(BASE + path, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = urlopen(req)
        return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


code, resp = post_json("/ui/close", {"ticket_id": 1})
assert code == 200 and resp.get("ok"), f"close failed: {code} {resp}"

c = db.connect()
t = c.execute("SELECT * FROM ticket WHERE id=1").fetchone()
d = c.execute("SELECT * FROM decision WHERE id=1").fetchone()
lease = c.execute("SELECT 1 FROM lease WHERE ticket_id=1").fetchone()
c.close()

assert t["status"] == "done", f"closed ticket must be done, got {t['status']}"
assert t["claim_epoch"] == 4, "close must fence the in-flight worker (epoch bump)"
assert t["blocked_by_decision_id"] is None, "block pointer must be cleared"
assert lease is None, "lease must be dropped"
assert d["status"] != "pending" and d["consumed"] == 1, \
    "pending decision must be voided so it leaves the Approvals queue"

# A stale answer to the voided decision must be a no-op 409, not a resurrection.
code, resp = post_json("/ui/answer", {"decision_id": 1, "action": "approve", "note": ""})
assert code == 409, f"answering a voided decision must 409, got {code}"

# Closing again is a 409 no-op.
code, resp = post_json("/ui/close", {"ticket_id": 1})
assert code == 409, f"double-close must 409, got {code}"

print("PASSED: close ends the ticket, fences the worker, drops the lease, voids the decision")
