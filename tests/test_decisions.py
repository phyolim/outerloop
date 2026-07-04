# Self-contained: the /decisions + ticket UI surfaces errors as errors (no approve/reject),
# renders decision context as a readable summary (PR link, not raw JSON), and Retry/Dismiss
# move a failed ticket out of the error queue. FAKE mode, throwaway DB, no deps.
import os, sys, atexit, shutil, tempfile, threading, time, json
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("INBOX_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-dec-")
os.environ["INBOX_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from inbox import db
from inbox.web import Handler

db.init_db()
c = db.connect()

# A failed coding ticket (the "error doing the task" case) + a clean pending merge decision.
c.execute("INSERT INTO ticket(id,title,body,type,status,handler_state) VALUES"
          "(1,'Broken task','x','coding','failed',?)", (json.dumps({"pr_url": "http://pr/1"}),))
db.append_audit(c, "handler:coding", "failed", "gh merge failed for PR #7", ticket_id=1)
c.execute("INSERT INTO ticket(id,title,body,type,status) VALUES"
          "(2,'Good task','y','coding','blocked')")
c.execute("INSERT INTO decision(id,ticket_id,kind,question,context,status) VALUES"
          "(1,2,'merge','Merge PR #9?',?, 'pending')",
          (json.dumps({"pr_url": "http://pr/9", "diff_stat": "3 files +40-2",
                       "checks": "green", "checks_green": True}),))
c.close()

PORT = 8811
BASE = f"http://127.0.0.1:{PORT}"
srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)


def get(path):
    return urlopen(BASE + path).read().decode()


def post(path, **form):
    Request  # keep import used
    urlopen(Request(BASE + path, data=urlencode(form).encode(), method="POST")).read()


page = get("/decisions")

# The failed ticket shows as an error: the reason, its PR link, and NO approve/reject on it.
assert "gh merge failed for PR #7" in page, "error reason must be shown"
assert "http://pr/1" in page, "failed ticket's PR link must be shown"
assert page.count("value=approve") == 1, "approve must appear ONLY on the clean decision, not the error"
# The clean decision renders a readable summary (PR link + diff), not a raw JSON dump.
assert "http://pr/9" in page and "3 files +40-2" in page, "decision summary must show PR + diff"
assert '"pr_url"' not in page, "context must be summarized, not dumped as JSON"

# Retry sends the failed ticket back to active at its stage, resetting the stall counter.
post("/retry", ticket_id="1")
t = db.connect().execute("SELECT status, attempts FROM ticket WHERE id=1").fetchone()
assert t["status"] == "active" and t["attempts"] == 0, f"retry should re-activate, got {dict(t)}"
assert "Broken task" not in get("/decisions"), "retried ticket must leave the error queue"

# Dismiss closes a failed ticket (set it back to failed first, then dismiss).
c = db.connect(); c.execute("UPDATE ticket SET status='failed' WHERE id=1"); c.close()
post("/dismiss", ticket_id="1")
assert db.connect().execute("SELECT status FROM ticket WHERE id=1").fetchone()["status"] == "done"

srv.shutdown()
print("PASSED: errors shown as errors, decisions summarized, retry/dismiss work")
