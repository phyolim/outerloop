# Decision push: creating a decision via the gate op must POST (ntfy-style) to the
# configured notify_url — title carries the kind, body carries ticket + question. The
# send is off-thread and best-effort: a dead URL must not break the gate write.
import os, sys, atexit, shutil, tempfile, threading, time, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("INBOX_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-notify-")
os.environ["INBOX_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from inbox import config, db, gate
from inbox.context import Ctx

db.init_db()
c = db.connect()

got = []


class Catcher(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        got.append({"title": self.headers.get("Title"),
                    "body": self.rfile.read(n).decode()})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


PORT = 8825
srv = ThreadingHTTPServer(("127.0.0.1", PORT), Catcher)
threading.Thread(target=srv.serve_forever, daemon=True).start()
db.set_setting(c, "notify_url", f"http://127.0.0.1:{PORT}/inbox")

c.execute("INSERT INTO ticket(id,title,body,type,status) VALUES"
          "(1,'Ship the fix','x','coding','active')")

# A handler hitting the gate (the only way a decision is born) fires the push.
did = gate.require(Ctx(c, config, "t1"), {"id": 1}, "merge", "Merge PR #9?",
                   {"pr_url": "http://pr/9"}, "merging")
deadline = time.time() + 5
while not got and time.time() < deadline:
    time.sleep(0.05)
assert got, "notify POST never arrived"
assert got[0]["title"] == "Decision needed: merge"
assert "#1 Ship the fix" in got[0]["body"] and "Merge PR #9?" in got[0]["body"]
# ...and the gate write itself committed (the ticket is blocked on the decision).
t = c.execute("SELECT * FROM ticket WHERE id=1").fetchone()
assert t["status"] == "blocked" and t["blocked_by_decision_id"] == did

# A dead notify target must not break the gate: point at a closed port and require again.
srv.shutdown()
db.set_setting(c, "notify_url", "http://127.0.0.1:1/nope")
c.execute("INSERT INTO ticket(id,title,body,type,status) VALUES"
          "(2,'Another','x','coding','active')")
did2 = gate.require(Ctx(c, config, "t2"), {"id": 2}, "deploy", "Deploy?", {}, "deploying")
assert c.execute("SELECT status FROM ticket WHERE id=2").fetchone()["status"] == "blocked"
time.sleep(0.2)  # give the doomed sender thread a beat; it must swallow the error

# Empty notify_url (the default) = off: no crash, no send.
db.set_setting(c, "notify_url", "")
c.execute("INSERT INTO ticket(id,title,body,type,status) VALUES"
          "(3,'Quiet','x','coding','active')")
gate.require(Ctx(c, config, "t3"), {"id": 3}, "merge", "Merge?", {}, "merging")
assert len(got) == 1, "no extra notifications expected"

print("ok: gate fires ntfy push; dead/empty targets never affect the gate write")
