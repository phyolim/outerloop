"""config._find_bin precedence (env > settings.json > PATH > fallback paths > bare
name) and doctor.run_doctor persisting a discovered claude path to settings.json —
the fix for a hub started under a stripped launchd env (e.g. `brew services`, which
bakes no OUTERLOOP_CLAUDE_BIN, unlike the .pkg installer) never finding a per-user
~/.local/bin/claude. FAKE, throwaway."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-binres-")
os.environ["OUTERLOOP_FAKE"] = "1"

from outerloop import config, doctor

tmp = Path(tempfile.mkdtemp(prefix="inbox-binres-fallback-"))
fallback_claude = tmp / "fallback" / "claude"
fallback_claude.parent.mkdir(parents=True)
fallback_claude.touch()

# --- PATH/which empty, no env, no setting -> falls back to the known install path ---
found = config._find_bin("OUTERLOOP_NOPE", "nope_bin", "nope-binary-xyz", (fallback_claude,))
assert found == str(fallback_claude), f"expected fallback path, got {found!r}"

# --- nothing resolves anywhere -> bare name (so a clear FileNotFoundError surfaces) ---
found = config._find_bin("OUTERLOOP_NOPE", "nope_bin", "nope-binary-xyz", ())
assert found == "nope-binary-xyz", f"expected bare name, got {found!r}"

# --- settings.json wins over an unresolvable PATH and beats the fallback list ---
config.set_local("claude_bin", "/settings/claude")
found = config._find_bin("OUTERLOOP_NOPE_2", "claude_bin", "nope-binary-xyz", (fallback_claude,))
assert found == "/settings/claude", f"settings.json should win, got {found!r}"

# --- env var wins over settings.json ---
os.environ["OUTERLOOP_NOPE_2"] = "/env/claude"
found = config._find_bin("OUTERLOOP_NOPE_2", "claude_bin", "nope-binary-xyz", (fallback_claude,))
assert found == "/env/claude", f"env var should win over settings.json, got {found!r}"
del os.environ["OUTERLOOP_NOPE_2"]

print("OK config._find_bin precedence: env > settings.json > PATH > fallback > bare name")

# --- doctor persists a discovered claude path to settings.json (brew/launchd-safe) ---
# config.HOME/SETTINGS_FILE are bound at import time from OUTERLOOP_HOME, so reassign
# SETTINGS_FILE directly to get a fresh, isolated settings.json for this phase.
config.SETTINGS_FILE = Path(tempfile.mkdtemp(prefix="inbox-binres-doctor-")) / "settings.json"
assert config.local_setting("claude_bin") is None, "fresh settings.json must start empty"


def run_all_ok(argv):
    if argv[1:3] == ["config", "--get"]:
        return 0, "me@example.com\n" if argv[-1] == "user.email" else "Me\n"
    if argv[1:3] == ["auth", "status"]:
        return 0, "logged in"
    return 0, ""


which = {"git": "/bin/git", "gh": "/bin/gh", "claude": "/bin/claude-real"}.get
doctor.checks(run=run_all_ok, which=which)  # pure: must NOT persist anything itself
assert config.local_setting("claude_bin") is None, "checks() must stay side-effect-free"

# run_doctor() is the CLI-facing entrypoint that persists the discovered path.
run_doctor_rc = doctor.run_doctor(run=run_all_ok, which=which)
assert run_doctor_rc == 0, "all real-mode prereqs stubbed ok -> doctor must exit 0"
assert config.local_setting("claude_bin") == "/bin/claude-real", \
    "run_doctor must persist the discovered claude path to settings.json"

print("OK doctor.run_doctor persists claude_bin to settings.json; checks() stays pure")
print("\n=== BIN RESOLUTION TEST PASSED ===")
