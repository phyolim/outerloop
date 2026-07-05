"""set_local / local_setting round-trip: the machine-local settings.json store that
`outerloop local` writes and the worker + `service` role-dispatch read. FAKE, throwaway."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-local-")
os.environ["OUTERLOOP_FAKE"] = "1"

from outerloop import config

# default when unset
assert config.local_setting("role", "hub") == "hub", "missing key must return default"

# write, read back
config.set_local("role", "worker")
assert config.local_setting("role") == "worker"

# read-modify-write preserves prior keys (doesn't clobber the file)
config.set_local("hub_url", "http://hub.local:8765")
assert config.local_setting("role") == "worker", "second write must not drop the first key"
assert config.local_setting("hub_url") == "http://hub.local:8765"

# overwrite in place
config.set_local("role", "hub")
assert config.local_setting("role") == "hub"

print("OK set_local/local_setting round-trip (role/hub_url) — settings.json persists")
print("\n=== LOCAL SETTINGS TEST PASSED ===")
