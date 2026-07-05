# Self-contained: (1) the stream-json -> event mapping used for the live ticket feed,
# (2) a FAKE run persists agent_event rows the ticket page can render. No deps.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-events-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import config, db
from outerloop.agent import _dispatch_event, run_agent
from outerloop.context import Ctx

# --- 1. parser: assistant text + tool_use + tool_result stream to events; the
#        result envelope is captured, not emitted.
got, state = [], {"texts": [], "result": None}
on = lambda k, b: got.append((k, b))
_dispatch_event({"type": "system", "subtype": "init"}, on, state)
_dispatch_event({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "Reading the file."},
    {"type": "tool_use", "name": "Read", "input": {"path": "a.py"}}]}}, on, state)
_dispatch_event({"type": "user", "message": {"content": [
    {"type": "tool_result", "content": [{"type": "text", "text": "line1"}]}]}}, on, state)
_dispatch_event({"type": "result", "result": '{"summary": "done"}',
                 "usage": {"input_tokens": 5, "output_tokens": 7}}, on, state)
assert got == [("text", "Reading the file."), ("tool", 'Read {"path": "a.py"}'),
               ("tool_result", "line1")], got
assert state["texts"] == ["Reading the file."]
assert state["result"]["result"] == '{"summary": "done"}'

# --- 2. FAKE run_agent writes an agent_event row alongside the agent_run row.
db.init_db()
conn = db.connect()
conn.execute("INSERT INTO ticket(title, body, type) VALUES('t', 'b', 'coding')")
res = run_agent(Ctx(conn, config, "tick-test"), "author", "do the thing", ticket_id=1)
rows = conn.execute("SELECT * FROM agent_event WHERE ticket_id=1").fetchall()
assert len(rows) == 1 and rows[0]["kind"] == "text" and rows[0]["role"] == "author", \
    [dict(r) for r in rows]
assert rows[0]["session_id"] == res["session_id"]
conn.close()

print("ok: agent event stream + persistence")
