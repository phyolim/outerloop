"""git_ops.ensure_local_clone + create_worktree exit-code handling: a ticket's
repo_path may be a bare remote URL (a human pointed a ticket at an existing repo via
the ticket-creation form, which takes repo_path as free text with no clone step —
the create_repo gate only fires when repo_path is EMPTY). Every other git_ops call
assumes repo_path is already a local clone, so a URL there silently broke `git -C
<url> ...` — and create_worktree discarded git's exit code entirely, so the failure
only surfaced later as a confusing FileNotFoundError on the never-created worktree
path. FAKE, throwaway: `_run` is monkeypatched, no real git/gh required."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-repoclone-")
os.environ["OUTERLOOP_FAKE"] = "1"

from outerloop import config, git_ops


class _Cfg:
    FAKE = False
    GH_BIN = "gh"
    GIT_BIN = "git"


class _Ctx:
    cfg = _Cfg()


ctx = _Ctx()

# --- already a local path (or none): passes through unchanged, no subprocess call ---
git_ops._run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not shell out"))
local, err = git_ops.ensure_local_clone(ctx, {"repo_path": "/already/local/clone"})
assert (local, err) == ("/already/local/clone", None)
local, err = git_ops.ensure_local_clone(ctx, {"repo_path": None})
assert (local, err) == (None, None)
print("ok: local/empty repo_path passes through with no clone attempt")

# --- a remote URL clones into REPOS_DIR, keyed by repo name ---
cloned = {}


def fake_run_clone_ok(cfg, argv, cwd=None):
    assert argv[:3] == ["gh", "repo", "clone"]
    dest = argv[4]
    os.makedirs(dest, exist_ok=True)
    cloned["dest"] = dest
    return 0, "", ""


git_ops._run = fake_run_clone_ok
local, err = git_ops.ensure_local_clone(
    ctx, {"repo_path": "https://github.com/phyolim/inbox-4-simple-tic-tac-toe-game"})
assert err is None, err
assert local == str(config.REPOS_DIR / "inbox-4-simple-tic-tac-toe-game")
print("ok: URL repo_path cloned into REPOS_DIR")

# --- idempotent: a second call finds the existing clone, no subprocess call ---
git_ops._run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-clone"))
local2, err2 = git_ops.ensure_local_clone(
    ctx, {"repo_path": "https://github.com/phyolim/inbox-4-simple-tic-tac-toe-game"})
assert (local2, err2) == (local, None)
print("ok: ensure_local_clone is idempotent — no re-clone once the dir exists")

# --- clone failure surfaces as an error, not a silently-wrong path ---
git_ops._run = lambda cfg, argv, cwd=None: (128, "", "repository not found")
local3, err3 = git_ops.ensure_local_clone(ctx, {"repo_path": "https://github.com/x/does-not-exist"})
assert local3 is None and "not found" in err3
print("ok: a failed clone returns a clear error instead of a bogus local path")

# --- create_worktree: `git worktree add` failure must surface, not silently no-op ---
git_ops._run = lambda cfg, argv, cwd=None: (1, "", "fatal: not a git repository")
path, err = git_ops.create_worktree(
    ctx, {"id": 99, "repo_path": "/some/local/clone"}, "outerloop/ticket-99-deadbeef")
assert path is None and "not a git repository" in err, (path, err)
print("ok: create_worktree surfaces git's exit code instead of pretending success")

# --- create_worktree: success returns the path with no error ---
def fake_run_wt_ok(cfg, argv, cwd=None):
    dest = argv[argv.index("add") + 1]
    os.makedirs(dest, exist_ok=True)
    return 0, "", ""


git_ops._run = fake_run_wt_ok
path, err = git_ops.create_worktree(
    ctx, {"id": 100, "repo_path": "/some/local/clone"}, "outerloop/ticket-100-cafef00d")
assert err is None and path.exists()
print("ok: create_worktree returns the path on success")

print("\n=== REPO CLONE / WORKTREE TEST PASSED ===")
