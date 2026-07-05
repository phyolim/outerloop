"""status role/mode resolution from settings.json (the pure part; daemon/hub probes are
IO and skipped here). FAKE, throwaway."""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-status-")
os.environ["OUTERLOOP_FAKE"] = "1"

from outerloop import config, status

# default role is hub, mode reflects FAKE
config.set_local("role", "hub")
buf = io.StringIO()
with redirect_stdout(buf):
    status.run_status()
out = buf.getvalue()
assert "role: hub" in out and "mode: FAKE" in out, out
assert "hub:" not in out, "hub-only box must not print a hub-reachability line"

# hub role surfaces the dashboard password (the daemon announces a generated one only in
# the service log — status is where a user actually finds it)
from outerloop import db
conn = db.init_db()
db.set_setting(conn, "ui_token", "hunter2")
db.set_setting(conn, "require_auth", "on")
buf = io.StringIO()
with redirect_stdout(buf):
    status.run_status()
out = buf.getvalue()
assert "dashboard password: hunter2" in out and "api auth: on" in out, out

# worker must NOT print the hub's dashboard password line
config.set_local("role", "worker")
buf = io.StringIO()
with redirect_stdout(buf):
    status.run_status()
assert "dashboard password" not in buf.getvalue()
config.set_local("role", "hub")

# worker + hub_url -> a hub-reachability line appears (unreachable is fine offline)
config.set_local("role", "worker")
config.set_local("hub_url", "http://127.0.0.1:1")   # nothing there -> unreachable, fast
buf = io.StringIO()
with redirect_stdout(buf):
    status.run_status()
out = buf.getvalue()
assert "role: worker" in out and "hub_url: http://127.0.0.1:1" in out, out
assert "hub: unreachable" in out, out

print("OK status — role/mode from settings.json; worker prints hub reachability")
print("\n=== STATUS TEST PASSED ===")
