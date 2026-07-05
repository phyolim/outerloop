"""Hub-role defaults: declaring role=hub flips real mode on by default, and an exposed
(non-loopback) hub auto-hardens — auth on + a dashboard password — with no manual step.
Precedence still lets env/local override. Throwaway home."""
import os
import sys
import importlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("OUTERLOOP_FAKE", None)   # so role/local decide, not env
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-hubdef-")

import outerloop.config as config  # noqa: E402
importlib.reload(config)

# unconfigured box: FAKE-safe
assert config.FAKE is True, "no role -> FAKE-safe default"

# declaring a hub -> real by default (no env, no local fake)
config.set_local("role", "hub")
importlib.reload(config)
assert config.FAKE is False, "role=hub must default to real mode"

# explicit override still wins over the role default
config.set_local("fake", "1")
importlib.reload(config)
assert config.FAKE is True, "local fake=1 must override the hub real-default"
os.environ["OUTERLOOP_FAKE"] = "1"; config.set_local("fake", "0")
importlib.reload(config)
assert config.FAKE is True, "OUTERLOOP_FAKE env must win over everything"
os.environ.pop("OUTERLOOP_FAKE", None)

# exposed-hub hardening: off -> on + a generated password, idempotent
from outerloop import db, hub  # noqa: E402
db.init_db()
conn = db.connect()
assert db.get_setting(conn, "require_auth", "off") != "on", "starts unauthenticated"
assert not db.get_setting(conn, "ui_token", ""), "starts with no dashboard password"

hub._harden_exposed_hub()
conn = db.connect()
assert db.get_setting(conn, "require_auth") == "on", "exposed hub must enable auth"
pw = db.get_setting(conn, "ui_token")
assert pw, "exposed hub must set a dashboard password"

hub._harden_exposed_hub()  # again — must not clobber the existing password
assert db.get_setting(db.connect(), "ui_token") == pw, "hardening must be idempotent"

print("OK hub defaults — role=hub is real; exposed hub auto-enables auth + a board password")
print("\n=== HUB DEFAULTS TEST PASSED ===")
