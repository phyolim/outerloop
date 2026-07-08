# Self-contained: hub + 3 workers on one box over loopback, FAKE mode. No deps.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-fleet-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
from outerloop import db as _bdb
_bdb.init_db()
# --- test body ---
import json, threading, time
from http.server import ThreadingHTTPServer
from outerloop import db, client
from outerloop.hub import HubHandler, scheduler_once
from outerloop import worker as W

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
WORKERS = {"pro": ["dev", "repos:*", "heavy"], "air": ["light", "mobile"],
           "mini": ["market-data", "analysis", "always-on"]}

srv = ThreadingHTTPServer(("127.0.0.1", PORT), HubHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)


def file_ticket(**kw):
    return client.post(BASE, "/api/tickets", kw)["id"]


def run_worker(worker):
    os.environ["OUTERLOOP_WORKER"] = worker
    os.environ["OUTERLOOP_CAPABILITIES"] = json.dumps(WORKERS[worker])
    os.environ["OUTERLOOP_HUB"] = BASE
    os.environ.pop("OUTERLOOP_WORKER_TOKEN", None)
    return W.run_worker_once()


def approve_pending():
    c = db.connect()
    rows = c.execute("SELECT * FROM decision WHERE status='pending'").fetchall()
    for d in rows:
        with db.immediate(c):
            c.execute("UPDATE decision SET status='approved', answered_at=datetime('now') WHERE id=?",
                      (d["id"],))
            c.execute("UPDATE ticket SET status='active' WHERE id=?", (d["ticket_id"],))
    c.close()
    return len(rows)


print("=== fleet drive (hub + pro/air/mini over loopback, FAKE) ===")
cod = file_ticket(title="Add retry to the HTTP client", body="exp backoff", type="coding")
kno = file_ticket(title="Research vector DB options", body="pgvector vs qdrant", type="knowledge")
junk = file_ticket(title="test junk asdf", body="", type="coding")

for i in range(40):
    scheduler_once()                       # hub: triage, score, reclaim, resume decisions
    for d in WORKERS:
        run_worker(d)                      # each worker: heartbeat, claim a match, run one stage
    approve_pending()                      # human approves merges/deploys
    c = db.connect()
    alive = c.execute("SELECT COUNT(*) n FROM ticket WHERE status IN ('inbox','active','blocked')").fetchone()["n"]
    c.close()
    if alive == 0:
        print(f"all terminal after {i+1} rounds")
        break

print("\n=== assertions ===")
c = db.connect()
def st(tid):
    return c.execute("SELECT status, sub_stage FROM ticket WHERE id=?", (tid,)).fetchone()
assert st(cod)["status"] == "done", f"coding not done: {tuple(st(cod))}"
assert st(kno)["status"] == "done", f"knowledge not done: {tuple(st(kno))}"
assert st(junk)["status"] == "parked", f"junk not parked: {tuple(st(junk))}"
print("OK terminal states: coding=done knowledge=done junk=parked (over the network)")

claims = c.execute("SELECT json_extract(detail,'$.worker') dev FROM audit"
                   " WHERE ticket_id=? AND action='claimed'", (cod,)).fetchall()
devs = {r["dev"] for r in claims}
assert devs == {"pro"}, f"coding claimed by {devs}, expected only pro (requires=[dev])"
print(f"OK routing: coding ticket claimed ONLY by pro, across {len(claims)} stages")

runs = c.execute("SELECT role, session_id FROM agent_run WHERE ticket_id=?", (cod,)).fetchall()
authors = {r["session_id"] for r in runs if r["role"] == "author"}
reviewers = {r["session_id"] for r in runs if r["role"] == "reviewer"}
assert authors and reviewers and not (authors & reviewers), f"author/reviewer overlap: {authors} {reviewers}"
print(f"OK author!=reviewer holds across the network ({len(authors)} author, {len(reviewers)} reviewer sessions)")

md = c.execute("SELECT status FROM decision WHERE ticket_id=? AND kind='merge'", (cod,)).fetchall()
assert md and all(x["status"] == "approved" for x in md), "merge not via hub-approved decision"
print("OK gate stayed hub-central: merge only via an approved decision")
c.close()

# --- fencing: a write with a stale epoch is rejected (must-fix #1/#6) ---
kno2 = file_ticket(title="Quick note", body="x", type="knowledge")
scheduler_once()  # -> active
claimed = client.post(BASE, "/api/claim", {"worker": "air"})
assert claimed.get("ticket"), "expected air to claim kno2"
tid, ep = claimed["ticket"]["id"], claimed["epoch"]
# simulate a reclaim + re-claim by bumping the ticket's monotonic fence
c = db.connect()
with db.immediate(c):
    c.execute("UPDATE ticket SET claim_epoch=claim_epoch+1 WHERE id=?", (tid,))
c.close()
try:
    client.post(BASE, "/api/op", {"op": "save_hs", "ticket_id": tid, "epoch": ep,
                                  "handler_state": "{}", "actor": "worker",
                                  "action": "x", "reason": "stale write"})
    raise SystemExit("FAIL: stale-epoch write was accepted")
except client.APIError as e:
    assert e.code == 409, f"expected 409 StaleEpoch, got {e.code}"
print("OK fencing: a write at a superseded epoch is rejected (409) — no stale double-write")

# --- renew (#6): a valid lease extends; a reclaimed one is rejected before any effect ---
kno3 = file_ticket(title="Yet another note", body="x", type="knowledge")
scheduler_once()
cl = client.post(BASE, "/api/claim", {"worker": "mini"})
assert cl.get("ticket"), "expected mini to claim kno3"
rtid, rep = cl["ticket"]["id"], cl["epoch"]
assert client.post(BASE, "/api/lease/renew", {"ticket_id": rtid, "epoch": rep}).get("ok"), "valid renew"
c = db.connect()  # simulate the scheduler reclaiming the (TTL-expired) lease
with db.immediate(c):
    c.execute("DELETE FROM lease WHERE ticket_id=?", (rtid,))
c.close()
try:
    client.post(BASE, "/api/lease/renew", {"ticket_id": rtid, "epoch": rep})
    raise SystemExit("FAIL: stale renew was accepted")
except client.APIError as e:
    assert e.code == 409, f"expected 409 on stale renew, got {e.code}"
print("OK renew (#6): valid lease extends; a reclaimed lease is rejected before a merge")

# --- caps are HUB-owned: a heartbeat never clobbers them; set_caps (the fleet UI) assigns work ---
client.post(BASE, "/api/heartbeat", {"worker": "pro", "capabilities": ["JUNK"], "version": "z"})
c = db.connect()
pcaps = lambda: json.loads(c.execute("SELECT capabilities FROM worker WHERE name='pro'").fetchone()[0])
assert pcaps() == WORKERS["pro"], f"heartbeat clobbered hub-owned caps: {pcaps()}"
client.post(BASE, "/api/worker/pro/control", {"action": "set_caps", "capabilities": ["dev", "repos:*"]})
assert pcaps() == ["dev", "repos:*"], f"set_caps (fleet UI) did not apply: {pcaps()}"
client.post(BASE, "/api/heartbeat", {"worker": "pro", "capabilities": ["JUNK"], "version": "z"})
assert pcaps() == ["dev", "repos:*"], f"a later heartbeat reverted the human's caps: {pcaps()}"
c.close()
print("OK caps hub-owned: heartbeat never clobbers; set_caps (fleet UI) is authoritative")

# --- fleet behavior is hub-owned too: a worker inherits FAKE/merge-policy/model routing
# from every heartbeat, so one Mac's stray OUTERLOOP_FAKE can't fake a real fleet ---
from outerloop import config as _cfg
hb = client.post(BASE, "/api/heartbeat", {"worker": "pro", "capabilities": [], "version": "z"})
assert hb["cfg"]["FAKE"] is True and hb["cfg"]["MODELS"].get("author"), f"heartbeat missing hub cfg: {hb}"
_cfg.FAKE = False                     # pretend this worker's local env said real mode
_cfg.apply_hub_cfg(hb["cfg"])
assert _cfg.FAKE is True, "worker did not inherit the hub's FAKE flag"
assert _cfg.resolve_model("author") == hb["cfg"]["MODELS"]["author"], "hub model routing not applied"
_cfg.apply_hub_cfg(None)              # an old hub (no cfg) must be a no-op
assert _cfg.FAKE is True
_cfg.HUB_MODELS = {}                  # restore for the rest of the file
print("OK hub-owned cfg: worker inherits FAKE + model routing from the heartbeat")

# --- pairing (fleet UI /worker-pair): the issued token authenticates ONLY under its own
# worker name — the exact name/token coupling a mismatched worker gets 403 on ---
import secrets
from outerloop import auth
c = db.connect()
db.set_setting(c, "require_auth", "on")
tok = secrets.token_hex(24)
auth.set_token(c, "laptop", tok)                     # what web._pair does
assert auth.resolve(c, tok) == "laptop", "paired token must resolve to its worker"
assert auth.resolve(c, "wrong") is None, "an unknown token resolves to nothing"
# name+token must agree (this is the check the API enforces on every heartbeat)
assert auth.resolve(c, tok) != "pro", "a laptop token must not authenticate as another worker"
db.set_setting(c, "require_auth", "off")
c.close()
print("OK pairing: issued token authenticates only under its own worker name")

# --- stranded pins: an active ticket pinned to a long-offline worker is parked
# (revivable), instead of sitting 'active' forever with no worker able to claim it ---
from outerloop import leasing
c = db.connect()
c.execute("INSERT INTO ticket(title, body, type, status, pin, requires)"
          " VALUES('stranded', '', 'knowledge', 'active', 'ghost', '[]')")
sid = c.execute("SELECT id FROM ticket WHERE title='stranded'").fetchone()["id"]
c.execute("INSERT INTO worker(name, last_seen) VALUES('ghost',"
          " datetime('now', '-48 hours'))")
parked = leasing.park_stranded(c, "test")
assert sid in parked, "ticket pinned to a 48h-offline worker must be parked"
row = c.execute("SELECT status, park_reason FROM ticket WHERE id=?", (sid,)).fetchone()
assert row["status"] == "parked" and "offline" in row["park_reason"]
# a pin to a RECENTLY-seen worker is left alone
c.execute("UPDATE worker SET last_seen=datetime('now') WHERE name='ghost'")
c.execute("UPDATE ticket SET status='active' WHERE id=?", (sid,))
assert leasing.park_stranded(c, "test") == [], "a live pinned worker must not be parked"
c.close()
print("OK stranded pin: parked after the offline window, untouched while the worker is live")

# --- fleet UI edit: rename carries the row, lease, assignment, and pin along;
# a collision with an existing worker is refused ---
c = db.connect()
with db.immediate(c):
    c.execute("INSERT INTO worker(name, capabilities, token_hash) VALUES('mbp','[\"dev\"]','th')")
    c.execute("INSERT INTO ticket(title, body, type, status, pin, requires)"
              " VALUES('pinned-to-mbp', '', 'knowledge', 'active', 'mbp', '[]')")
    ptid = c.execute("SELECT id FROM ticket WHERE title='pinned-to-mbp'").fetchone()["id"]
    c.execute("INSERT INTO lease(ticket_id, owner, pid, boot_uuid, expires_at, worker)"
              " VALUES(?, 'tick-x', 1, 'bu', datetime('now','+10 minutes'), 'mbp')", (ptid,))
c.close()
try:
    client.post(BASE, "/api/worker/mbp/control", {"action": "rename", "new_name": "pro"})
    raise SystemExit("FAIL: rename onto an existing worker was accepted")
except client.APIError as e:
    assert e.code == 409, f"expected 409 on name collision, got {e.code}"
client.post(BASE, "/api/worker/mbp/control", {"action": "rename", "new_name": "studio"})
c = db.connect()
row = c.execute("SELECT token_hash FROM worker WHERE name='studio'").fetchone()
assert row and row["token_hash"] == "th", "renamed row must keep its token"
assert not c.execute("SELECT 1 FROM worker WHERE name='mbp'").fetchone(), "old name lingers"
assert c.execute("SELECT worker FROM lease WHERE ticket_id=?", (ptid,)).fetchone()["worker"] == "studio"
assert c.execute("SELECT pin FROM ticket WHERE id=?", (ptid,)).fetchone()["pin"] == "studio"
c.close()
print("OK rename: row+token, lease, and pin all follow the new name; collisions 409")

# --- fleet UI delete: row + token gone, in-flight lease fenced (epoch bump) and
# freed, unleased pinned tickets parked (revivable) instead of routed anywhere ---
c = db.connect()
ep0 = c.execute("SELECT claim_epoch FROM ticket WHERE id=?", (ptid,)).fetchone()["claim_epoch"]
with db.immediate(c):
    c.execute("INSERT INTO ticket(title, body, type, status, pin, requires)"
              " VALUES('pinned-unleased', '', 'knowledge', 'active', 'studio', '[]')")
    utid = c.execute("SELECT id FROM ticket WHERE title='pinned-unleased'").fetchone()["id"]
c.close()
client.post(BASE, "/api/worker/studio/control", {"action": "delete"})
c = db.connect()
assert not c.execute("SELECT 1 FROM worker WHERE name='studio'").fetchone(), "worker row survived delete"
assert not c.execute("SELECT 1 FROM lease WHERE ticket_id=?", (ptid,)).fetchone(), "lease survived delete"
t = c.execute("SELECT claim_epoch, assigned_worker FROM ticket WHERE id=?", (ptid,)).fetchone()
assert t["claim_epoch"] == ep0 + 1 and t["assigned_worker"] is None, "in-flight ticket not fenced"
u = c.execute("SELECT status, park_reason FROM ticket WHERE id=?", (utid,)).fetchone()
assert u["status"] == "parked" and "removed" in u["park_reason"], f"pinned ticket not parked: {tuple(u)}"
c.close()
try:
    client.post(BASE, "/api/worker/studio/control", {"action": "delete"})
    raise SystemExit("FAIL: deleting a missing worker was accepted")
except client.APIError as e:
    assert e.code == 404, f"expected 404 on double delete, got {e.code}"
print("OK delete: token revoked with the row, lease fenced+freed, pinned work parked")

print("\n=== FLEET MULTI-NODE TESTS PASSED ===")
srv.shutdown()
