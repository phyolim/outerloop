# Self-contained: hub + 3 workers on one box over loopback, FAKE mode. No deps.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("INBOX_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-fleet-")
os.environ["INBOX_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
from inbox import db as _bdb
_bdb.init_db()
# --- test body ---
import json, threading, time
from http.server import ThreadingHTTPServer
from inbox import db, client
from inbox.coordinator import CoordHandler, scheduler_once
from inbox import worker as W

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
WORKERS = {"pro": ["dev", "repos:*", "heavy"], "air": ["light", "mobile"],
           "mini": ["market-data", "analysis", "always-on"]}

srv = ThreadingHTTPServer(("127.0.0.1", PORT), CoordHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)


def file_ticket(**kw):
    return client.post(BASE, "/api/tickets", kw)["id"]


def run_worker(device):
    os.environ["INBOX_DEVICE"] = device
    os.environ["INBOX_CAPABILITIES"] = json.dumps(WORKERS[device])
    os.environ["INBOX_HUB"] = BASE
    os.environ.pop("INBOX_DEVICE_TOKEN", None)
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

claims = c.execute("SELECT json_extract(detail,'$.device') dev FROM audit"
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
claimed = client.post(BASE, "/api/claim", {"device": "air"})
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
cl = client.post(BASE, "/api/claim", {"device": "mini"})
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
client.post(BASE, "/api/heartbeat", {"device": "pro", "capabilities": ["JUNK"], "version": "z"})
c = db.connect()
pcaps = lambda: json.loads(c.execute("SELECT capabilities FROM device WHERE name='pro'").fetchone()[0])
assert pcaps() == WORKERS["pro"], f"heartbeat clobbered hub-owned caps: {pcaps()}"
client.post(BASE, "/api/device/pro/control", {"action": "set_caps", "capabilities": ["dev", "repos:*"]})
assert pcaps() == ["dev", "repos:*"], f"set_caps (fleet UI) did not apply: {pcaps()}"
client.post(BASE, "/api/heartbeat", {"device": "pro", "capabilities": ["JUNK"], "version": "z"})
assert pcaps() == ["dev", "repos:*"], f"a later heartbeat reverted the human's caps: {pcaps()}"
c.close()
print("OK caps hub-owned: heartbeat never clobbers; set_caps (fleet UI) is authoritative")

# --- fleet behavior is hub-owned too: a worker inherits FAKE/merge-policy/model routing
# from every heartbeat, so one Mac's stray INBOX_FAKE can't fake a real fleet ---
from inbox import config as _cfg
hb = client.post(BASE, "/api/heartbeat", {"device": "pro", "capabilities": [], "version": "z"})
assert hb["cfg"]["FAKE"] is True and hb["cfg"]["MODELS"].get("author"), f"heartbeat missing hub cfg: {hb}"
_cfg.FAKE = False                     # pretend this worker's local env said real mode
_cfg.apply_hub_cfg(hb["cfg"])
assert _cfg.FAKE is True, "worker did not inherit the hub's FAKE flag"
assert _cfg.resolve_model("author") == hb["cfg"]["MODELS"]["author"], "hub model routing not applied"
_cfg.apply_hub_cfg(None)              # an old hub (no cfg) must be a no-op
assert _cfg.FAKE is True
_cfg.HUB_MODELS = {}                  # restore for the rest of the file
print("OK hub-owned cfg: worker inherits FAKE + model routing from the heartbeat")

# --- pairing (fleet UI /device-pair): the issued token authenticates ONLY under its own
# device name — the exact name/token coupling a mismatched worker gets 403 on ---
import secrets
from inbox import auth
c = db.connect()
db.set_setting(c, "require_auth", "on")
tok = secrets.token_hex(24)
auth.set_token(c, "laptop", tok)                     # what web._pair does
assert auth.resolve(c, tok) == "laptop", "paired token must resolve to its device"
assert auth.resolve(c, "wrong") is None, "an unknown token resolves to nothing"
# name+token must agree (this is the check the API enforces on every heartbeat)
assert auth.resolve(c, tok) != "pro", "a laptop token must not authenticate as another device"
db.set_setting(c, "require_auth", "off")
c.close()
print("OK pairing: issued token authenticates only under its own device name")

# --- stranded pins: an active ticket pinned to a long-offline device is parked
# (revivable), instead of sitting 'active' forever with no device able to claim it ---
from inbox import leasing
c = db.connect()
c.execute("INSERT INTO ticket(title, body, type, status, pin, requires)"
          " VALUES('stranded', '', 'knowledge', 'active', 'ghost', '[]')")
sid = c.execute("SELECT id FROM ticket WHERE title='stranded'").fetchone()["id"]
c.execute("INSERT INTO device(name, last_seen) VALUES('ghost',"
          " datetime('now', '-48 hours'))")
parked = leasing.park_stranded(c, "test")
assert sid in parked, "ticket pinned to a 48h-offline device must be parked"
row = c.execute("SELECT status, park_reason FROM ticket WHERE id=?", (sid,)).fetchone()
assert row["status"] == "parked" and "offline" in row["park_reason"]
# a pin to a RECENTLY-seen device is left alone
c.execute("UPDATE device SET last_seen=datetime('now') WHERE name='ghost'")
c.execute("UPDATE ticket SET status='active' WHERE id=?", (sid,))
assert leasing.park_stranded(c, "test") == [], "a live pinned device must not be parked"
c.close()
print("OK stranded pin: parked after the offline window, untouched while the device is live")

print("\n=== FLEET MULTI-NODE TESTS PASSED ===")
srv.shutdown()
