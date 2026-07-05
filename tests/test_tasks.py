# Self-contained: exercises GET /api/tasks + POST /api/tasks/{id}/terminate directly
# against an in-process FAKE db. No HTTP server, no deps.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-tasks-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import db, api

db.init_db()
conn = db.connect()

# A coding ticket mid-flight: active/fixing, leased by worker "air" at epoch 5, with one
# agent_run recording the session + worktree the app needs to tail/kill.
with db.immediate(conn):
    conn.execute("INSERT INTO ticket(id,title,type,status,sub_stage,claim_epoch,score)"
                 " VALUES(2,'build a tic tac toe game','coding','active','fixing',5,24.0)")
    conn.execute("INSERT INTO lease(ticket_id,owner,pid,boot_uuid,expires_at,epoch,worker)"
                 " VALUES(2,'air-x',0,'b',datetime('now','+5 minutes'),5,'air')")
    conn.execute("INSERT INTO agent_run(id,ticket_id,role,tick_id,session_id,prompt,"
                 "worktree_path,cost_usd) VALUES('sess-1',2,'fixer','t1','sess-1','p',"
                 "'/wt/ticket-2-abc',0.0)")

status, out = api.handle("GET", "/api/tasks", {}, conn)
assert status == 200, status
tasks = out["tasks"]
assert len(tasks) == 1, tasks
t = tasks[0]
assert t["id"] == 2 and t["running"] is True and t["worker"] == "air", t
assert t["session_id"] == "sess-1" and t["worktree_path"] == "/wt/ticket-2-abc", t
print("OK /api/tasks: leased ticket shows running worker + session + worktree")

status, out = api.handle("POST", "/api/tasks/2/terminate", {}, conn)
assert status == 200 and out.get("ok"), (status, out)
row = conn.execute("SELECT status,claim_epoch FROM ticket WHERE id=2").fetchone()
assert row["status"] == "parked", row["status"]                 # not re-claimable
assert row["claim_epoch"] == 6, row["claim_epoch"]              # fenced: in-flight worker aborts
assert conn.execute("SELECT 1 FROM lease WHERE ticket_id=2").fetchone() is None
print("OK terminate: ticket parked, epoch fenced (5->6), lease dropped")

# A terminated (parked) ticket still appears in the list, now not running.
status, out = api.handle("GET", "/api/tasks", {}, conn)
t = out["tasks"][0]
assert t["running"] is False and t["worker"] is None, t
print("OK parked ticket still listed, running=False")

status, out = api.handle("POST", "/api/tasks/999/terminate", {}, conn)
assert status == 404, status
print("OK terminate unknown ticket -> 404")

print("\n=== TASKS API TEST PASSED ===")
