# Self-contained: Pause stops an active ticket in place — it fences the in-flight
# worker (epoch bump), drops the lease, and parks it with sub_stage intact so Resume
# re-enters the same stage. FAKE mode, throwaway DB, no deps.
import os, sys, atexit, shutil, tempfile, threading, time, json
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-pause-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import db
from outerloop.web import Handler

db.init_db()
c = db.connect()
# An active ticket mid-stage with a live lease (worker mid-flight).
c.execute("INSERT INTO ticket(id,title,body,type,status,sub_stage,claim_epoch,attempts)"
          " VALUES(1,'Big feature','x','coding','active','coding',3,2)")
c.execute("INSERT INTO lease(ticket_id,owner,pid,boot_uuid,expires_at) VALUES"
          "(1,'tick-x',1,'boot',datetime('now','+10 minutes'))")
# A triage-parked ticket (never ran: sub_stage NULL) — Resume must refuse it.
c.execute("INSERT INTO ticket(id,title,body,type,status,park_reason) VALUES"
          "(2,'Someday','x','coding','parked','low score')")
c.close()

PORT = 8814
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


code, resp = post_json("/ui/pause", {"ticket_id": 1})
assert code == 200 and resp.get("ok"), f"pause failed: {code} {resp}"

c = db.connect()
t = c.execute("SELECT * FROM ticket WHERE id=1").fetchone()
lease = c.execute("SELECT 1 FROM lease WHERE ticket_id=1").fetchone()
c.close()

assert t["status"] == "parked", f"paused ticket must be parked, got {t['status']}"
assert t["park_reason"] == "paused by human"
assert t["claim_epoch"] == 4, "pause must fence the in-flight worker (epoch bump)"
assert t["sub_stage"] == "coding", "pause must keep sub_stage so Resume re-enters it"
assert lease is None, "lease must be dropped"

# Pausing again (no longer active) is a 409 no-op.
code, resp = post_json("/ui/pause", {"ticket_id": 1})
assert code == 409, f"double-pause must 409, got {code}"

# An in-flight LOCAL stage (claimed at the old epoch) must be fenced too — its next
# write raises StaleEpoch instead of overwriting the pause with status='active'.
from outerloop import config
from outerloop.context import Ctx, StaleEpoch

c = db.connect()
ctx = Ctx(c, config, "tick-test")
ctx.epoch = 3  # what run_tick stamped when it claimed, before the pause bumped to 4
try:
    ctx.write("set_stage", ticket_id=1, status="active", sub_stage="reviewing",
              handler_state="{}", actor="agent", action="stage", reason="done coding")
    raise AssertionError("stale local write must raise StaleEpoch, not land")
except StaleEpoch:
    pass
t = c.execute("SELECT status FROM ticket WHERE id=1").fetchone()
assert t["status"] == "parked", "fenced write must not undo the pause"

# The reaper must spare a paused (parked + sub_stage) ticket's worktree — Resume
# re-enters the same stage and needs the workspace — while still reaping the
# triage-parked one (never ran, nothing to keep).
from outerloop import git_ops
config.WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
(config.WORKTREES_DIR / "ticket-1").mkdir(exist_ok=True)
(config.WORKTREES_DIR / "ticket-2").mkdir(exist_ok=True)
git_ops.reap_worktrees(ctx)
assert (config.WORKTREES_DIR / "ticket-1").exists(), \
    "paused ticket's worktree must survive the reaper"
assert not (config.WORKTREES_DIR / "ticket-2").exists(), \
    "triage-parked ticket's worktree must still be reaped"
c.close()

code, resp = post_json("/ui/resume", {"ticket_id": 1})
assert code == 200 and resp.get("ok"), f"resume failed: {code} {resp}"

c = db.connect()
t = c.execute("SELECT * FROM ticket WHERE id=1").fetchone()
c.close()
assert t["status"] == "active", f"resumed ticket must be active, got {t['status']}"
assert t["park_reason"] is None
assert t["sub_stage"] == "coding", "resume must re-enter the paused stage"
assert t["attempts"] == 0, "resume must reset the stall counter"

# Resume means nothing for a triage-parked ticket that never ran.
code, resp = post_json("/ui/resume", {"ticket_id": 2})
assert code == 409, f"resuming a never-run parked ticket must 409, got {code}"

print("PASSED: pause fences the worker and parks in place; resume re-enters the stage")
