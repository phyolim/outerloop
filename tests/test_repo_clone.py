"""git_ops.local_repo + create_worktree exit-code handling. repo_path is CANONICAL,
machine-independent ticket state (remote URL or a human-typed local path) and is never
rewritten to a machine-local clone path: each worker resolves it locally on demand.
(The old ensure_local_clone wrote the claiming box's clone path into the shared ticket
— mbp cloned, then mini claimed the retry and failed on mbp's path.) Also: a git
worktree-add failure must surface its stderr, not silently return the never-created
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

# --- an existing local path (or empty) passes through, no subprocess call ---
git_ops._run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not shell out"))
localdir = tempfile.mkdtemp(prefix="inbox-repoclone-local-")
repo, err = git_ops.local_repo(ctx, {"repo_path": localdir})
assert (repo, err) == (localdir, None)
repo, err = git_ops.local_repo(ctx, {"repo_path": None})
assert (repo, err) == (None, None)
print("ok: existing local / empty repo_path passes through with no clone attempt")

# --- a local path that does NOT exist on this machine errors clearly (a stale
# machine-local path from another box must not survive silently) ---
repo, err = git_ops.local_repo(ctx, {"repo_path": "/gone/on/this/machine"})
assert repo is None and "does not exist on this machine" in err
print("ok: a foreign/missing local path errors instead of limping into git -C")

# --- a remote URL clones into REPOS_DIR, keyed by repo name ---
def fake_run_clone_ok(cfg, argv, cwd=None):
    assert argv[:3] == ["gh", "repo", "clone"]
    os.makedirs(argv[4], exist_ok=True)
    return 0, "", ""


git_ops._run = fake_run_clone_ok
url = "https://github.com/phyolim/inbox-4-simple-tic-tac-toe-game"
repo, err = git_ops.local_repo(ctx, {"repo_path": url})
assert err is None, err
assert repo == str(config.REPOS_DIR / "inbox-4-simple-tic-tac-toe-game")
print("ok: URL repo_path resolved to a machine-local clone in REPOS_DIR")

# --- idempotent: a second call finds the existing clone, no subprocess call ---
git_ops._run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-clone"))
repo2, err2 = git_ops.local_repo(ctx, {"repo_path": url})
assert (repo2, err2) == (repo, None)
print("ok: local_repo is idempotent — no re-clone once the dir exists")

# --- clone failure surfaces as an error, not a silently-wrong path ---
git_ops._run = lambda cfg, argv, cwd=None: (128, "", "repository not found")
repo3, err3 = git_ops.local_repo(ctx, {"repo_path": "https://github.com/x/does-not-exist"})
assert repo3 is None and "not found" in err3
print("ok: a failed clone returns a clear error instead of a bogus local path")

# --- create_worktree: `git worktree add` failure must surface, not silently no-op ---
git_ops._run = lambda cfg, argv, cwd=None: (1, "", "fatal: not a git repository")
path, err = git_ops.create_worktree(
    ctx, {"id": 99, "repo_path": url}, "outerloop/ticket-99-deadbeef", "/some/local/clone")
assert path is None and "not a git repository" in err, (path, err)
print("ok: create_worktree surfaces git's exit code instead of pretending success")

# --- create_worktree: success returns the path with no error ---
def fake_run_wt_ok(cfg, argv, cwd=None):
    os.makedirs(argv[argv.index("add") + 1], exist_ok=True)
    return 0, "", ""


git_ops._run = fake_run_wt_ok
path, err = git_ops.create_worktree(
    ctx, {"id": 100, "repo_path": url}, "outerloop/ticket-100-cafef00d", "/some/local/clone")
assert err is None and path.exists()
print("ok: create_worktree returns the path on success")

# --- create_repo returns the canonical URL (from the clone's origin), never the
# machine-local clone path ---
def fake_run_create(cfg, argv, cwd=None):
    if argv[:3] == ["gh", "repo", "create"]:
        os.makedirs(os.path.join(str(cwd), argv[3]), exist_ok=True)
        return 0, "", ""
    if argv[-3:] == ["remote", "get-url", "origin"]:
        return 0, "https://github.com/phyolim/inbox-42-shiny-thing.git\n", ""
    raise AssertionError(f"unexpected argv {argv}")


git_ops._run = fake_run_create
url42, err = git_ops.create_repo(ctx, {"id": 42, "title": "shiny thing"})
assert err == "" and url42 == "https://github.com/phyolim/inbox-42-shiny-thing", (url42, err)
print("ok: create_repo records the canonical URL, not this machine's clone path")

print("\n=== REPO CLONE / WORKTREE TEST PASSED ===")
