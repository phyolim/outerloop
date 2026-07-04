# Drafts: a UI-added ticket is an unsubmitted idea — triage/scoring must NOT touch it
# until the human starts it (/ui/start or the /start form). FAKE mode, throwaway DB.
import os, sys, atexit, shutil, tempfile, threading, time, json
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("INBOX_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-draft-")
os.environ["INBOX_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from inbox import api, config, db, triage
from inbox.context import Ctx
from inbox.web import Handler

db.init_db()
c = db.connect()

PORT = 8823
BASE = f"http://127.0.0.1:{PORT}"
srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)


def post_json(path, obj):
    req = Request(BASE + path, data=json.dumps(obj).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = urlopen(req)
        return r.status, json.loads(r.read())
    except Exception as e:  # HTTPError has .code/.read
        return e.code, json.loads(e.read())


def post_form(path, **form):
    urlopen(Request(BASE + path, data=urlencode(form).encode(), method="POST")).read()


def row(tid):
    return c.execute("SELECT * FROM ticket WHERE id=?", (tid,)).fetchone()


# 1. /ui/add defaults to draft; triage skips it entirely.
st, r = post_json("/ui/add", {"title": "half-baked idea", "kind": "feature"})
assert st == 200 and r["draft"] is True, r
tid = r["id"]
triage.triage_new(Ctx(c, config, "t1"))
t = row(tid)
assert t["status"] == "inbox" and t["draft"] == 1, "triage must not touch a draft"

# 2. Board JSON flags it so the UI can render the draft chip + Start button.
board = json.loads(urlopen(BASE + "/ui/board.json").read())
card = next(x for x in board["columns"]["inbox"] if x["id"] == tid)
assert card["draft"] is True

# 3. Start submits it; the next triage pass activates it like any new ticket.
st, r = post_json("/ui/start", {"id": tid})
assert st == 200 and r["ok"] is True
triage.triage_new(Ctx(c, config, "t2"))
assert row(tid)["status"] == "active", "started draft must triage to active"

# 4. Starting a non-draft is a 409 no-op.
st, r = post_json("/ui/start", {"id": tid})
assert st == 409, "re-start must be rejected"

# 5. "start now" paths skip the draft stage: /ui/add draft:false and the /add checkbox.
st, r = post_json("/ui/add", {"title": "go immediately", "kind": "chore", "draft": False})
assert r["draft"] is False and row(r["id"])["draft"] == 0
post_form("/add", title="form start now", kind="feature", start="1")
post_form("/add", title="form default draft", kind="feature")
frm = {t["title"]: t for t in c.execute("SELECT * FROM ticket").fetchall()}
assert frm["form start now"]["draft"] == 0
assert frm["form default draft"]["draft"] == 1

# 6. Producer/API path is unchanged: /api/tickets defaults to NOT draft (screener must
#    keep flowing), and draft:true is available for callers that want it.
st, r = api.handle("POST", "/api/tickets", {"title": "producer ticket"}, c)
assert row(r["id"])["draft"] == 0
st, r = api.handle("POST", "/api/tickets", {"title": "api draft", "draft": True}, c)
assert row(r["id"])["draft"] == 1

srv.shutdown()
print("ok: drafts invisible to triage until started; start-now paths skip the stage")
