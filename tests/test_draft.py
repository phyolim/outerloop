# Drafts: a UI-added ticket is an unsubmitted idea — triage/scoring must NOT touch it
# until the human starts it (/ui/start). FAKE mode, throwaway DB.
import os, sys, atexit, shutil, tempfile, threading, time, json
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-draft-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import api, config, db, triage
from outerloop.context import Ctx
from outerloop.web import Handler

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

# 5. The "start now" path skips the draft stage: /ui/add with draft:false.
st, r = post_json("/ui/add", {"title": "go immediately", "kind": "chore", "draft": False})
assert r["draft"] is False and row(r["id"])["draft"] == 0
st, r = post_json("/ui/add", {"title": "default draft", "kind": "feature"})
assert r["draft"] is True and row(r["id"])["draft"] == 1

# 6. Producer/API path is unchanged: /api/tickets defaults to NOT draft (screener must
#    keep flowing), and draft:true is available for callers that want it.
st, r = api.handle("POST", "/api/tickets", {"title": "producer ticket"}, c)
assert row(r["id"])["draft"] == 0
st, r = api.handle("POST", "/api/tickets", {"title": "api draft", "draft": True}, c)
assert row(r["id"])["draft"] == 1

# 7. /ui/edit works on a NON-draft too (title/body/project/repo_path — the human must
#    be able to fix a bad repo URL and retry), but kind/type stay structural: the
#    handler's sub_stage belongs to the current lifecycle, so those edits are ignored.
st, r = post_json("/ui/edit", {"ticket_id": tid, "title": "renamed in-pipeline",
                               "kind": "research", "body": "clarified",
                               "project": "proj-x", "repo_path": "/tmp/some-repo"})
assert st == 200, r
t = row(tid)
assert t["title"] == "renamed in-pipeline" and t["project"] == "proj-x"
assert t["kind"] == "feature" and t["type"] == "coding", "kind/type must not change post-draft"
assert t["repo_path"] == "/tmp/some-repo"

# 8. While a worker holds a live lease the edit is refused (mid-stage, content in use).
c.execute("INSERT INTO lease(ticket_id, owner, pid, boot_uuid, worker, epoch,"
          " claim_sub_stage, claim_status, expires_at)"
          " VALUES(?,?,?,?,?,?,?,?, datetime('now','+30 minutes'))",
          (tid, "w1", 0, "w1", "w1", 1, None, "active"))
c.commit()
st, r = post_json("/ui/edit", {"ticket_id": tid, "title": "no"})
assert st == 409 and "worker" in r["error"], r
c.execute("DELETE FROM lease WHERE ticket_id=?", (tid,))
c.commit()

# 9. A done ticket can't be edited; an unknown id is a 404.
c.execute("UPDATE ticket SET status='done' WHERE id=?", (tid,))
c.commit()
st, r = post_json("/ui/edit", {"ticket_id": tid, "title": "no"})
assert st == 409, r
st, r = post_json("/ui/edit", {"ticket_id": 99999, "title": "no"})
assert st == 404, r

srv.shutdown()
print("ok: drafts invisible to triage until started; start-now paths skip the stage;"
      " in-pipeline edits allowed except mid-lease/done")
