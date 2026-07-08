"""'Agent does, code verifies' seam in git_ops: the agent performs clone/worktree/
commit/push/PR-create inside its stage prompt; this module only VERIFIES outcomes and
executes the gated merge. repo_path stays CANONICAL, machine-independent ticket state
(remote URL or a human-typed local path) — never rewritten to one box's clone path
(a ticket that bounced mbp -> mini failed exactly that way once). FAKE, throwaway:
`_run` is monkeypatched, no real git/gh required."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-verify-")
os.environ["OUTERLOOP_FAKE"] = "1"

from outerloop import git_ops


class _Cfg:
    FAKE = False
    GH_BIN = "gh"
    GIT_BIN = "git"


class _Ctx:
    cfg = _Cfg()


ctx = _Ctx()
URL = "https://github.com/phyolim/inbox-4-simple-tic-tac-toe-game"

# --- gh_slug: owner/name out of every canonical repo_path shape ---
for rp in (URL, URL + ".git", URL + "/", "git@github.com:phyolim/inbox-4-simple-tic-tac-toe-game.git"):
    slug, err = git_ops.gh_slug(ctx, {"repo_path": rp})
    assert (slug, err) == ("phyolim/inbox-4-simple-tic-tac-toe-game", None), (rp, slug, err)

# a local path reads its origin remote
localdir = tempfile.mkdtemp(prefix="inbox-verify-local-")
git_ops._run = lambda cfg, argv, cwd=None: (0, URL + ".git", "")
slug, err = git_ops.gh_slug(ctx, {"repo_path": localdir})
assert (slug, err) == ("phyolim/inbox-4-simple-tic-tac-toe-game", None)

# a local path missing on THIS machine errors clearly (stale cross-machine path)
slug, err = git_ops.gh_slug(ctx, {"repo_path": "/gone/on/this/machine"})
assert slug is None and "does not exist on this machine" in err
print("ok: gh_slug parses URLs/ssh/local-origin; foreign local paths error clearly")

# --- clone_name / worktree_path are deterministic ---
assert git_ops.clone_name(URL + ".git/") == "inbox-4-simple-tic-tac-toe-game"
wt = git_ops.worktree_path({"id": 8}, "outerloop/ticket-8-abcd1234")
assert wt.name == "ticket-8-abcd1234"
print("ok: clone_name/worktree_path deterministic")

# --- verify_worktree: trusts git, not the agent ---
T8 = {"id": 8, "repo_path": URL}
HS = {"worktree_path": "/some/wt", "branch": "outerloop/ticket-8-abcd1234"}

git_ops._run = lambda cfg, argv, cwd=None: (128, "", "fatal: not a git repository")
base, err = git_ops.verify_worktree(ctx, T8, HS)
assert base is None and "no git worktree" in err

git_ops._run = lambda cfg, argv, cwd=None: (0, "main", "")
base, err = git_ops.verify_worktree(ctx, T8, HS)
assert base is None and "expected" in err, "wrong branch must fail verification"


def run_wt_ok(cfg, argv, cwd=None):
    if argv[-1] == "origin/HEAD":
        return 0, "origin/main", ""
    return 0, HS["branch"], ""


git_ops._run = run_wt_ok
base, err = git_ops.verify_worktree(ctx, T8, HS)
assert (base, err) == ("origin/main", None)
print("ok: verify_worktree checks branch + derives the diff base")

# --- verify_pr: reads the PR from GitHub via -R, never trusts the agent's claim ---
def run_pr_ok(cfg, argv, cwd=None):
    assert "-R" in argv and "phyolim/inbox-4-simple-tic-tac-toe-game" in argv
    return 0, '{"number": 12, "url": "https://github.com/x/pull/12"}', ""


git_ops._run = run_pr_ok
num, url, err = git_ops.verify_pr(ctx, T8, HS["branch"])
assert (num, url, err) == (12, "https://github.com/x/pull/12", None)

git_ops._run = lambda cfg, argv, cwd=None: (1, "", "no pull requests found")
num, url, err = git_ops.verify_pr(ctx, T8, HS["branch"])
assert num is None and "no pull requests" in err
print("ok: verify_pr resolves via gh -R and surfaces a missing PR")

# --- the gated side needs NO local clone: checks/state/merge all go through -R ---
calls = []


def run_gated(cfg, argv, cwd=None):
    calls.append(argv)
    assert cwd is None and "-R" in argv, f"gated call must use -R, not a local cwd: {argv}"
    if argv[1] == "pr" and argv[2] == "checks":
        return 0, '[{"state": "SUCCESS"}]', ""
    if argv[1] == "pr" and argv[2] == "view":
        return 0, '{"state": "OPEN"}', ""
    if argv[1] == "pr" and argv[2] == "merge":
        return 0, "", ""
    raise AssertionError(argv)


git_ops._run = run_gated
hs = {"pr_number": 12}
assert git_ops.checks_green(ctx, T8, hs) == (True, "all checks SUCCESS")
assert git_ops.pr_state(ctx, T8, hs) == "open"
assert git_ops.merge_pr(ctx, T8, hs) is True
assert len(calls) == 3
print("ok: checks/state/merge run clone-free via gh -R")

# --- create_repo returns the canonical URL, never this machine's clone path ---
def run_create(cfg, argv, cwd=None):
    if argv[:3] == ["gh", "repo", "create"]:
        os.makedirs(os.path.join(str(cwd), argv[3]), exist_ok=True)
        return 0, "", ""
    if argv[-3:] == ["remote", "get-url", "origin"]:
        return 0, "https://github.com/phyolim/inbox-42-shiny-thing.git\n", ""
    raise AssertionError(argv)


git_ops._run = run_create
url42, err = git_ops.create_repo(ctx, {"id": 42, "title": "shiny thing"})
assert err == "" and url42 == "https://github.com/phyolim/inbox-42-shiny-thing", (url42, err)
print("ok: create_repo records the canonical URL, not this machine's clone path")

print("\n=== VERIFY-SEAM TEST PASSED ===")
