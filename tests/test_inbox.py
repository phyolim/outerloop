# Self-contained: the v2 Inbox + Board JSON seams. inbox.json surfaces in-progress
# runs (worker · lease age · latest transcript line) and a today digest of recent
# outcomes (merged→ok, fail→bad, parked→muted); tickets.json returns every ticket in
# one flat list with the filter-chip counts (open = backlog+active+blocked; on-hold
# included; done sliced). FAKE mode, throwaway DB, no deps.
import atexit
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-v2-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import db, web

db.init_db()
c = db.connect()
h = web.Handler.__new__(web.Handler)


def add(tid, title, kind, status, sub=None, score=None, draft=0):
    c.execute("INSERT INTO ticket(id,title,body,type,kind,status,sub_stage,score,draft)"
              " VALUES(?,?,?,?,?,?,?,?,?)",
              (tid, title, "b", "coding", kind, status, sub, score, draft))


add(1, "Add dark mode", "feature", "inbox", draft=1)        # backlog (draft)
add(2, "Fix flaky retry", "bug", "inbox", "seed", 18)        # backlog
add(3, "Refactor fence", "feature", "active", "implemented", 24)  # in progress
add(4, "Bump heartbeat", "chore", "blocked", "merge_gate", 24)    # blocked
add(5, "Dup reports", "bug", "parked")                       # on hold
add(6, "Token ceiling", "feature", "done", "merged", 30)     # done
add(7, "Bonjour rebind", "bug", "done", "merged", 21)        # done
add(8, "Nightly backup", "ops", "failed", "run")             # failed — must stay visible
# running detail: lease age + latest transcript line for #3
c.execute("INSERT INTO lease(ticket_id,owner,pid,boot_uuid,expires_at,worker,acquired_at)"
          " VALUES(3,'t',0,'u',datetime('now','+5 minutes'),'hub',datetime('now','-31 minutes'))")
c.execute("INSERT INTO agent_event(ticket_id,session_id,role,kind,body)"
          " VALUES(3,'s','author','tool','ran a tool'),"
          "        (3,'s','author','text','test_leasing passes — 14/14.')")
# today digest: an outcome of each tone
db.append_audit(c, "handler:coding", "merged", "merged, 3/3 checks green", ticket_id=6)
db.append_audit(c, "handler:coding", "fail", "agent timed out 3x", ticket_id=4)
db.append_audit(c, "triage", "parked", "junk: duplicate of #2", ticket_id=5)
c.commit()

# an active ticket NOBODY holds a lease on, requiring 'dev'
add(9, "Port the scraper", "chore", "active", "seed", 12)
c.execute("UPDATE ticket SET requires='[\"dev\"]' WHERE id=9")
c.commit()

# --- inbox.json ---
ib = h._inbox_json(c)
assert ib["drafts"] == 1, ib  # ticket 1 is the only draft
assert {r["id"] for r in ib["running"]} == {3, 9}, ib["running"]
byrun = {r["id"]: r for r in ib["running"]}
r = byrun[3]
assert r["worker"] == "hub", r
assert r["last_line"] == "test_leasing passes — 14/14.", r["last_line"]  # latest TEXT event, not the tool
# leased = running; unleased with NO worker rows at all (fleet-less install) = queued
assert r["state"] == "running" and byrun[9]["state"] == "queued", byrun
# an online worker WITHOUT the required cap -> unclaimable; WITH it -> queued
c.execute("INSERT INTO worker(name,capabilities,status,last_seen)"
          " VALUES('mini','[\"market-data\"]','online',datetime('now'))")
c.commit()
assert {r["id"]: r["state"] for r in h._inbox_json(c)["running"]}[9] == "unclaimable"
c.execute("UPDATE worker SET capabilities='[\"dev\"]' WHERE name='mini'")
c.commit()
assert {r["id"]: r["state"] for r in h._inbox_json(c)["running"]}[9] == "queued"
byid = {d["id"]: d for d in ib["digest"]}
assert byid[6]["dot"] == "ok" and byid[4]["dot"] == "bad" and byid[5]["dot"] == "muted", ib["digest"]
assert byid[6]["what"] == "merged, 3/3 checks green"

# --- tickets.json ---
tk = h._tickets_json(c)
assert len(tk["tickets"]) == 9, len(tk["tickets"])
assert sorted({t["status"] for t in tk["tickets"]}) == \
    ["active", "blocked", "done", "failed", "inbox", "parked"]
co = tk["counts"]
# failed counts as OPEN — the default Board view must not hide unresolved work
assert co == {"backlog": 2, "active": 2, "blocked": 1, "onhold": 1, "failed": 1, "done": 2,
              "open": 6, "all": 9}, co
# blocked ticket carries its decision-kind wait hint (None here — no decision row), draft flag present
draft = next(t for t in tk["tickets"] if t["id"] == 1)
assert draft["draft"] is True and draft["status"] == "inbox"
# worker: a leased active ticket names its machine; unleased or non-active carry none
bycard = {t["id"]: t for t in tk["tickets"]}
assert bycard[3]["worker"] == "hub", bycard[3]
assert bycard[9]["worker"] is None and bycard[6]["worker"] is None, (bycard[9], bycard[6])
# ticket.json mirrors it for the detail page's meta rail
assert h._ticket_json(c, 3)["ticket"]["worker"] == "hub"
assert h._ticket_json(c, 6)["ticket"]["worker"] is None

# --- project filter narrows both counts and rows ---
c.execute("UPDATE ticket SET project='homelab' WHERE id IN (2,5)")
c.commit()
tk2 = h._tickets_json(c, project="homelab")
assert {t["id"] for t in tk2["tickets"]} == {2, 5}, tk2["tickets"]
assert tk2["counts"] == {"backlog": 1, "active": 0, "blocked": 0, "onhold": 1, "failed": 0,
                         "done": 0, "open": 1, "all": 2}, tk2["counts"]

# --- repos: deduped recently-used repo_path values for the create-form autocomplete ---
c.execute("UPDATE ticket SET repo_path='https://github.com/me/old' WHERE id=1")
c.execute("UPDATE ticket SET repo_path='https://github.com/me/new' WHERE id IN (2,7)")
c.commit()
assert h._tickets_json(c)["repos"] == \
    ["https://github.com/me/new", "https://github.com/me/old"], h._tickets_json(c)["repos"]

c.close()
print("PASSED: inbox running/digest tones + tickets flat list, counts, project filter")
