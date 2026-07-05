"""`setup-hub` one-command config: a real LAN hub with auth on and a dashboard password.
Asserts every knob it's meant to flip — the safe 'exposed hub' bundle in one place."""
import os
import sys
import importlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("OUTERLOOP_FAKE", None)   # let the local 'fake' setting decide
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-setuphub-")

import outerloop.config as config  # noqa: E402
importlib.reload(config)
from outerloop import db  # noqa: E402
from outerloop.__main__ import _setup_hub  # noqa: E402

_setup_hub("s3cret")

# machine-local runtime config -> a real LAN hub
assert config.local_setting("role") == "hub"
assert config.local_setting("bind") == "0.0.0.0"
assert config.local_setting("fake") == "0"
importlib.reload(config)
assert config.FAKE is False, "fake=0 must put the box in real mode"

# hub DB settings -> API auth on + dashboard password set
conn = db.connect()
assert db.get_setting(conn, "require_auth") == "on"
assert db.get_setting(conn, "ui_token") == "s3cret"

# blank password is allowed (documented as 'open') but still leaves auth on
_setup_hub("")
conn = db.connect()
assert db.get_setting(conn, "ui_token") == ""
assert db.get_setting(conn, "require_auth") == "on"

print("OK setup-hub — role/bind/fake + require_auth + ui_token all set")
print("\n=== SETUP-HUB TEST PASSED ===")
