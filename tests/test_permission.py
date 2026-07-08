# The permission bridge: a live run's tool-permission ask becomes a ticket-thread
# decision (never blocking the ticket), the human's Allow/Deny reaches the waiting
# run, unanswered asks expire, and the janitor sweeps asks whose run died.
# FAKE mode, throwaway DB, no deps, no real claude.
import atexit
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-perm-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import agent, api, db, gate, permission_mcp as pm

db.init_db()
c = db.connect()
c.execute("INSERT INTO ticket(id,title,body,type,status,claim_epoch) VALUES"
          "(1,'Perm ticket','x','coding','active',0)")
c.execute("INSERT INTO lease(ticket_id,owner,pid,boot_uuid,expires_at,epoch,worker)"
          " VALUES(1,'t1',1,'b1',datetime('now','+30 minutes'),0,'w1')")

CFG = {"ticket": 1, "epoch": 0, "hub": None, "token": None, "wait": 30}
ARGS = {"tool_name": "WebFetch", "input": {"url": "https://example.com"}}


def latest_decision():
    return c.execute("SELECT * FROM decision ORDER BY id DESC LIMIT 1").fetchone()


def answer(status, note="", rework=0):
    def _sleep(_):
        c.execute("UPDATE decision SET status=?, rework=?, answer_note=?,"
                  " answered_at=datetime('now') WHERE status='pending'",
                  (status, rework, note))
    return _sleep


# --- Allow: ask posts a pending decision, human approves, run gets allow ---
v = pm.decide(ARGS, CFG, sleep=answer("approved"))
assert v == {"behavior": "allow", "updatedInput": ARGS["input"]}, v
d = latest_decision()
assert d["kind"] == "permission" and "WebFetch" in d["question"]
t = c.execute("SELECT * FROM ticket WHERE id=1").fetchone()
assert t["status"] == "active" and t["blocked_by_decision_id"] is None, \
    "a permission ask must never block the ticket (the run is still live)"

# --- Deny: rejection (incl. 'Request changes' rework) carries the note back ---
v = pm.decide(ARGS, CFG, sleep=answer("rejected", note="use the local mirror", rework=1))
assert v["behavior"] == "deny" and "use the local mirror" in v["message"], v

# --- Timeout: no answer -> deny + the ask is voided so a late click is a no-op ---
v = pm.decide(ARGS, dict(CFG, wait=0), sleep=lambda s: None)
assert v["behavior"] == "deny" and '"question"' in v["message"], v
d = latest_decision()
assert d["status"] == "rejected" and d["consumed"] == 1, "unanswered ask must be voided"

# --- Stale epoch: a reclaimed run's ask is refused -> deny, nothing inserted ---
n_before = c.execute("SELECT COUNT(*) n FROM decision").fetchone()["n"]
v = pm.decide(ARGS, dict(CFG, epoch=99), sleep=lambda s: None)
assert v["behavior"] == "deny"
assert c.execute("SELECT COUNT(*) n FROM decision").fetchone()["n"] == n_before

# --- GET /api/decision/<id>: what a remote bridge polls ---
status, obj = api.handle("GET", f"/api/decision/{d['id']}", {}, c)
assert status == 200 and obj["status"] == "rejected" and obj["kind"] == "permission"
status, _ = api.handle("GET", "/api/decision/9999", {}, c)
assert status == 404

# --- Janitor: pending ask with a live lease stays; without one it expires ---
c.execute("INSERT INTO ticket(id,title,body,type,status) VALUES"
          "(2,'Dead run','x','coding','active')")
c.execute("INSERT INTO decision(ticket_id,kind,question) VALUES(2,'permission','allow X?')")
c.execute("INSERT INTO decision(ticket_id,kind,question) VALUES(1,'permission','allow Y?')")
gate.expire_orphan_permissions(c)
dead = c.execute("SELECT status,consumed FROM decision WHERE ticket_id=2").fetchone()
live = c.execute("SELECT status FROM decision WHERE ticket_id=1 AND question='allow Y?'").fetchone()
assert dead["status"] == "rejected" and dead["consumed"] == 1, "orphan ask must expire"
assert live["status"] == "pending", "an ask with a live lease must survive the sweep"
# ...and non-permission pending decisions are never the janitor's business.
c.execute("INSERT INTO ticket(id,title,body,type,status) VALUES"
          "(3,'Gated','x','coding','blocked')")
c.execute("INSERT INTO decision(ticket_id,kind,question) VALUES(3,'merge','merge?')")
gate.expire_orphan_permissions(c)
assert c.execute("SELECT status FROM decision WHERE ticket_id=3").fetchone()["status"] == "pending"

# --- agent wiring: leased runs get the bridge flags, with a valid inline config ---
from outerloop import config

argv = agent._perm_args(config, {"ticket_id": 1, "epoch": 0,
                                 "hub": "http://127.0.0.1:1", "token": "tk"})
assert argv[0] == "--mcp-config" and argv[2:] == ["--permission-prompt-tool",
                                                  "mcp__outerloop__approve"]
mcp = json.loads(argv[1])["mcpServers"]["outerloop"]
assert mcp["env"]["OUTERLOOP_PERM_TICKET"] == "1"
assert mcp["env"]["OUTERLOOP_PERM_HUB"] == "http://127.0.0.1:1"
assert mcp["args"] == ["-m", "outerloop.permission_mcp"]

# --- the stdio JSON-RPC framing end to end (no ticket ctx -> fast deny) ---
import subprocess

msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "approve", "arguments": ARGS}}]
env = {k: v for k, v in os.environ.items() if not k.startswith("OUTERLOOP_PERM")}
p = subprocess.run([sys.executable, "-m", "outerloop.permission_mcp"],
                   input="".join(json.dumps(m) + "\n" for m in msgs),
                   capture_output=True, text=True, timeout=30, env=env,
                   cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
replies = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
assert replies[1]["result"]["protocolVersion"] == "2025-06-18"
assert replies[2]["result"]["tools"][0]["name"] == "approve"
verdict = json.loads(replies[3]["result"]["content"][0]["text"])
assert verdict["behavior"] == "deny" and "no ticket" in verdict["message"]

c.close()
print("test_permission: OK")
