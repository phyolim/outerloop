"""doctor.checks() pure logic with a stubbed runner + which: present/absent binaries and
set/unset git identity, no real gh/claude required. FAKE, throwaway."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-doctor-")
os.environ["OUTERLOOP_FAKE"] = "1"

from outerloop import doctor


def by_name(results):
    return {c["name"]: c for c in results}


# --- all present + identity set + gh authed -> every check ok ---
present = {"git": "/bin/git", "gh": "/bin/gh", "claude": "/bin/claude"}


def run_all_ok(argv):
    if argv[1:3] == ["config", "--get"]:
        return 0, "me@example.com\n" if argv[-1] == "user.email" else "Me\n"
    if argv[1:3] == ["auth", "status"]:
        return 0, "logged in"
    return 0, ""


r = by_name(doctor.checks(run=run_all_ok, which=present.get))
assert r["git"]["ok"] and r["git commit identity"]["ok"]
assert r["gh"]["ok"] and r["gh authenticated"]["ok"]
assert r["claude"]["ok"], "claude present must pass"

# --- absent gh + claude -> those blockers fail, with fix hints ---
only_git = {"git": "/bin/git"}
r = by_name(doctor.checks(run=run_all_ok, which=only_git.get))
assert not r["gh"]["ok"] and r["gh"]["fix"], "absent gh must fail with a fix hint"
assert not r["claude"]["ok"], "absent claude must fail"
assert "gh authenticated" not in r, "no gh -> no auth sub-check"

# --- git present but identity unset -> identity check fails, git present passes ---
def run_no_identity(argv):
    if argv[1:3] == ["config", "--get"]:
        return 0, "\n"       # empty email AND name
    return 0, ""


r = by_name(doctor.checks(run=run_no_identity, which=present.get))
assert r["git"]["ok"], "git binary present"
assert not r["git commit identity"]["ok"], "unset identity must fail"

# --- gh present but not authed -> auth sub-check fails ---
def run_gh_unauthed(argv):
    if argv[1:3] == ["config", "--get"]:
        return 0, "me@example.com\n" if argv[-1] == "user.email" else "Me\n"
    if argv[1:3] == ["auth", "status"]:
        return 1, ""
    return 0, ""


r = by_name(doctor.checks(run=run_gh_unauthed, which=present.get))
assert r["gh"]["ok"] and not r["gh authenticated"]["ok"], "unauthed gh must fail auth sub-check"

print("OK doctor.checks — present/absent binaries + git identity + gh auth")
print("\n=== DOCTOR TEST PASSED ===")
