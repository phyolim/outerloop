"""role=both (the combined hub+worker node). Covers the two things that make it work with
no manual step: real-mode default (like a hub), and the co-located worker identity —
a stable name, a token that resolves to it even with auth on, and seeded capabilities so
it's actually eligible for work. Plus `setup-both` flipping the same hardened bundle as
setup-hub but with role=both. Throwaway home; no network, no daemon."""
import os
import sys
import json
import importlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("OUTERLOOP_FAKE", None)   # let role/local decide the mode
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-both-")

import outerloop.config as config  # noqa: E402
importlib.reload(config)

# role=both is a hub-side box: real by default, same as role=hub.
config.set_local("role", "both")
importlib.reload(config)
assert config.FAKE is False, "role=both must default to real mode, like a hub"

from outerloop import auth, db  # noqa: E402
import outerloop.combined as combined  # noqa: E402
importlib.reload(combined)

# Hardened path: auth ON before the co-located worker identity is ensured. This is the
# case a LAN-bound hub produces, and the co-located worker must still authenticate.
conn = db.init_db()
db.set_setting(conn, "require_auth", "on")
name = combined._ensure_local_identity(conn)

assert name, "combined node must derive a worker name"
assert config.local_setting("worker") == name, "worker name persisted to settings"

tok = config.local_setting("token")
assert tok, "a token must be minted for the co-located worker"
assert auth.resolve(conn, tok) == name, "the minted token must resolve to this worker (auth on -> no 403)"

row = conn.execute("SELECT capabilities FROM worker WHERE name=?", (name,)).fetchone()
caps = json.loads(row["capabilities"])
assert caps == combined.DEFAULT_CAPS, f"caps seeded so the worker gets work; got {caps}"

# Idempotent: a second call reuses the same identity, never re-mints or clobbers caps.
tok2_name = combined._ensure_local_identity(conn)
assert tok2_name == name
assert config.local_setting("token") == tok, "existing valid token is reused, not rotated"

# A pre-set worker name is honored (not overwritten by the hostname default).
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-both2-")
importlib.reload(config)
config.set_local("role", "both")
config.set_local("worker", "mini")
conn2 = db.init_db()
assert combined._ensure_local_identity(conn2) == "mini", "explicit worker name wins"

# setup-both flips the hardened hub bundle but with role=both.
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-both3-")
importlib.reload(config)
from outerloop.__main__ import _setup_hub  # noqa: E402
_setup_hub("pw", role="both")
assert config.local_setting("role") == "both"
assert config.local_setting("bind") == "0.0.0.0"
conn3 = db.connect()
assert db.get_setting(conn3, "require_auth") == "on"
assert db.get_setting(conn3, "ui_token") == "pw"

print("OK combined — real default, co-located worker minted+resolvable (auth on), caps seeded, idempotent, setup-both")
print("\n=== COMBINED NODE TEST PASSED ===")
