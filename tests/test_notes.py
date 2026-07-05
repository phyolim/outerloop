# Self-contained: an operator note added while a worker holds a ticket must survive
# the worker's whole-JSON handler_state writeback (set_stage / save_hs re-merge it),
# and /ui/comment threads the note into both the audit trail and handler_state.
# FAKE mode, throwaway DB, no deps.
import atexit
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-notes-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import config, context, db  # noqa: E402
from outerloop.web import Handler  # noqa: E402

db.init_db()
conn = db.connect()
conn.execute("INSERT INTO ticket(id,title,body,type,kind,status,sub_stage,draft)"
             " VALUES(1,'t','b','coding','feature','active','groomed',0)")


class _FakeReq:
    """Just enough Handler to call the write helpers without a socket."""
    def __init__(self):
        pass


h = Handler.__new__(Handler)

# 1) /ui/comment write path: note lands in audit AND handler_state.clarifications.
sent = {}
h._json_send = lambda obj, code=200: sent.update(obj=obj, code=code)
h._ui_dispatch(conn, "/ui/comment", {"ticket_id": 1, "note": "ship behind a flag"})
assert sent["code"] == 200 and sent["obj"] == {"ok": True}, sent
hs = json.loads(conn.execute("SELECT handler_state FROM ticket WHERE id=1").fetchone()[0])
assert hs["clarifications"] == [{"q": "(operator note)", "a": "ship behind a flag"}], hs
assert conn.execute("SELECT COUNT(*) FROM audit WHERE ticket_id=1"
                    " AND action='commented'").fetchone()[0] == 1

# 2) Worker writes back the hs it read BEFORE the note existed (whole-JSON, no note):
#    the op must re-merge the note instead of clobbering it.
ctx = context.Ctx(conn, config, "tick-test")
stale_hs = json.dumps({"branch": "t/1", "clarifications": [{"q": "real q", "a": "real a"}]})
ctx.write("set_stage", ticket_id=1, status="active", sub_stage="authored",
          handler_state=stale_hs, actor="handler:coding", action="stage", reason="test")
hs = json.loads(conn.execute("SELECT handler_state FROM ticket WHERE id=1").fetchone()[0])
assert {"q": "(operator note)", "a": "ship behind a flag"} in hs["clarifications"], hs
assert {"q": "real q", "a": "real a"} in hs["clarifications"], hs
assert hs["branch"] == "t/1", hs

# 3) save_hs path too, and no duplicate when the note IS already present.
ctx.write("save_hs", ticket_id=1, handler_state=json.dumps(hs),
          actor="handler:coding", action="note", reason="test")
hs2 = json.loads(conn.execute("SELECT handler_state FROM ticket WHERE id=1").fetchone()[0])
assert len([c for c in hs2["clarifications"] if c["q"] == "(operator note)"]) == 1, hs2

# 4) factors clamp: out-of-range values snap to 1..5.
h._factors(conn, {"ticket_id": 1, "impact": 99, "urgency": 0, "confidence": 3, "effort": -2})
t = conn.execute("SELECT impact,urgency,confidence,effort FROM ticket WHERE id=1").fetchone()
assert (t["impact"], t["urgency"], t["confidence"], t["effort"]) == (5, 1, 3, 1), dict(t)

print("PASSED: operator notes survive worker hs writeback; comment + clamp paths hold")
