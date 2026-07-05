"""The `fake` and `bind` machine-local settings that let a brew-services hub go real
and bind the LAN with no baked env. FAKE precedence: env > local 'fake' > default (safe)."""
import os
import sys
import importlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("OUTERLOOP_FAKE", None)   # so the local 'fake' setting is consulted
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-bindfake-")

import outerloop.config as config
importlib.reload(config)

# default-safe: no setting, no env -> FAKE
assert config.FAKE is True, "fresh box must default to FAKE"

# `outerloop local fake 0` -> real mode after (re)load
config.set_local("fake", "0")
importlib.reload(config)
assert config.FAKE is False, "local fake=0 must enable real mode"

# env always wins over the local setting (keeps tests / OUTERLOOP_FAKE=1 authoritative)
os.environ["OUTERLOOP_FAKE"] = "1"
importlib.reload(config)
assert config.FAKE is True, "OUTERLOOP_FAKE=1 must override local fake=0"

# `bind` round-trips (consumed by the `service` command -> run_hub host)
config.set_local("bind", "0.0.0.0")
assert config.local_setting("bind") == "0.0.0.0"

print("OK fake/bind local settings — precedence env > local > default")
print("\n=== BIND/FAKE TEST PASSED ===")
