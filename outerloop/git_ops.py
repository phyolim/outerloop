"""git worktree + gh wrappers. All subprocess calls are list-form (never shell=True),
so ticket-controlled text can't inject args or shell. Coding work is confined to a
per-ticket worktree on a throwaway, collision-proof branch; the only path to main is
the human-gated merge. FAKE mode simulates GitHub in handler_state so the lifecycle
runs with no repo."""

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from . import config, db

_REMOTE_URL_RE = re.compile(r"^(https?://|git@|ssh://)")


def _run(cfg, argv, cwd=None):
    """List-form only. Returns (returncode, stdout, stderr)."""
    p = subprocess.run(argv, cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def new_branch(ticket_id):
    return f"outerloop/ticket-{ticket_id}-{uuid.uuid4().hex[:8]}"


def repo_slug(ticket):
    """A GitHub-safe repo name from the ticket title. Sanitized to [a-z0-9-] so the
    ticket-controlled title can't smuggle anything into the gh argv (list-form already
    blocks shell injection; this keeps gh itself from rejecting the name)."""
    base = re.sub(r"[^a-z0-9]+", "-", (ticket["title"] or "").lower()).strip("-")
    return f"inbox-{ticket['id']}-{base or 'project'}"[:100]


def create_repo(ctx, ticket):
    """Create a new private GitHub repo for a ticket that has no repo_path and clone it
    locally, so the normal worktree/PR/merge flow runs against it unchanged. Human-gated
    upstream (never auto). --add-readme gives an initial commit so the default branch
    exists (git worktree add -b needs a base). Returns (canonical_url, error) — the
    ticket records the URL, never this machine's clone path (see local_repo)."""
    name = repo_slug(ticket)
    dest = config.REPOS_DIR / name
    if not dest.exists():
        config.REPOS_DIR.mkdir(parents=True, exist_ok=True)
        code, out, err = _run(ctx.cfg, [ctx.cfg.GH_BIN, "repo", "create", name,
                              "--private", "--add-readme", "--clone"], cwd=config.REPOS_DIR)
        # ponytail: idempotent retry guard — a prior attempt may have cloned already
        if code != 0 or not dest.exists():
            return None, (err or out or "gh repo create failed")
    code, out, err = _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", str(dest),
                          "remote", "get-url", "origin"])
    if code != 0 or not out:
        return None, (err or "could not read the new repo's origin URL")
    return re.sub(r"\.git$", "", out.strip()), ""


def local_repo(ctx, ticket):
    """Resolve the ticket's repo_path to a repo directory ON THIS MACHINE, cloning on
    demand. repo_path is CANONICAL, machine-independent state (a remote URL, or a
    local path the human typed) and is never rewritten to a machine-local clone path:
    in a fleet, whichever worker claims a stage resolves it locally — writing one
    box's clone path into the shared ticket strands every other box (a ticket that
    bounced mbp -> mini failed exactly that way). Returns (path_or_None, error_or_None);
    FAKE / empty repo_path passes through (callers keep their existing guards)."""
    repo_path = ticket["repo_path"]
    if ctx.cfg.FAKE or not repo_path:
        return repo_path, None
    if not _REMOTE_URL_RE.match(repo_path):
        if not Path(repo_path).exists():
            return None, (f"repo path does not exist on this machine: {repo_path}"
                          " (use the repo's URL so any worker can clone it)")
        return repo_path, None
    name = re.sub(r"\.git$", "", repo_path.rstrip("/")).rsplit("/", 1)[-1]
    dest = config.REPOS_DIR / name
    if dest.exists():  # idempotent: this machine already cloned it
        return str(dest), None
    config.REPOS_DIR.mkdir(parents=True, exist_ok=True)
    code, out, err = _run(ctx.cfg, [ctx.cfg.GH_BIN, "repo", "clone", repo_path, str(dest)])
    if code != 0 or not dest.exists():
        return None, (err or out or "gh repo clone failed")
    return str(dest), None


def repo_head(ctx, repo):
    """The repo's current branch — the base a new worktree branches from. Recorded so
    the reviewer can diff the branch locally (review happens BEFORE any PR exists).
    `repo` is the machine-local dir from local_repo()."""
    if ctx.cfg.FAKE or not repo:
        return "HEAD"
    _, out, _ = _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", repo,
                     "rev-parse", "--abbrev-ref", "HEAD"])
    return out or "HEAD"


def branch_diff(ctx, ticket, hs):
    """The branch's full diff vs its base (what a PR would show), read from the local
    worktree so the review/fix loop runs before the PR is opened."""
    if ctx.cfg.FAKE or not ticket["repo_path"]:
        return "diff --git a/src/feature.py b/src/feature.py\n+ fake change\n"
    base = hs.get("base_branch") or "HEAD"
    _, out, _ = _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", hs["worktree_path"],
                     "diff", f"{base}...HEAD"])
    return out


def create_worktree(ctx, ticket, branch, repo):
    """Idempotent: reuse an existing tree for this ticket if present. Returns
    (path_or_None, error_or_None) — a git failure must surface here, not as a
    confusing FileNotFoundError from whatever later tries to use the never-created
    path as a subprocess cwd (must-fix: `git worktree add`'s exit code was previously
    discarded entirely). `repo` is the machine-local dir from local_repo()."""
    path = config.WORKTREES_DIR / f"ticket-{ticket['id']}-{branch.rsplit('-', 1)[-1]}"
    if path.exists():
        return path, None
    if ctx.cfg.FAKE or not repo:
        path.mkdir(parents=True, exist_ok=True)
        return path, None
    config.WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    code, out, err = _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", repo,
                          "worktree", "add", str(path), "-b", branch])
    if code != 0 or not path.exists():
        return None, (err or out or "git worktree add failed")
    return path, None


def open_pr(ctx, ticket, hs):
    """Push the branch and open a PR. Returns (pr_number, pr_url)."""
    if ctx.cfg.FAKE or not ticket["repo_path"]:
        num = 1000 + ticket["id"]
        return num, f"https://example.invalid/pr/{num}"
    repo, err = local_repo(ctx, ticket)
    if err:
        return None, ""
    branch, wt = hs["branch"], hs["worktree_path"]
    _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", wt, "push", "-u", "origin", branch])
    # `gh pr create` has no --json; it prints the PR URL. Create, then read back the
    # number+url structurally with `gh pr view`.
    _run(ctx.cfg, [ctx.cfg.GH_BIN, "pr", "create", "--fill", "--head", branch], cwd=repo)
    _, out, _ = _run(ctx.cfg, [ctx.cfg.GH_BIN, "pr", "view", branch,
                     "--json", "number,url"], cwd=repo)
    data = json.loads(out) if out else {}
    return data.get("number"), data.get("url", "")


def commit_all(ctx, ticket, hs, message):
    """Commit whatever the author/fixer wrote in the worktree. Deterministic — we don't
    trust the agent to remember to commit. No-op in FAKE or when nothing is staged."""
    if ctx.cfg.FAKE or not ticket["repo_path"]:
        return
    wt = hs["worktree_path"]
    _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", wt, "add", "-A"])
    _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", wt, "commit", "-m", message])  # nonzero if nothing to commit; fine


# The status prefix checks_green returns when the repo has no CI at all. The merging
# stage matches on this to accept a human approval instead of re-gating forever
# (no-CI can never turn green, so re-asking is an unanswerable question).
NO_CI_STATUS = "no CI checks configured"


def checks_green(ctx, ticket, hs):
    """HARD merge precondition (must-fix #3). Returns (ok, status). 'no CI configured'
    is default-deny, NOT green-by-absence."""
    if ctx.cfg.FAKE or not ticket["repo_path"]:
        return True, "success (fake)"
    # "No CI configured" is default-deny, but an operator can opt in to merging repos
    # that have no Actions at all (OUTERLOOP_ALLOW_MERGE_WITHOUT_CI). This relaxes ONLY the
    # no-CI case — a repo with actually-failing checks still blocks below.
    allow_no_ci = getattr(ctx.cfg, "ALLOW_MERGE_WITHOUT_CI", False)
    no_ci_msg = NO_CI_STATUS + (
        " (merge allowed by OUTERLOOP_ALLOW_MERGE_WITHOUT_CI)" if allow_no_ci else "")
    repo, rerr = local_repo(ctx, ticket)
    if rerr:
        return False, f"could not resolve repo locally: {rerr}"
    code, out, err = _run(ctx.cfg, [ctx.cfg.GH_BIN, "pr", "checks", str(hs["pr_number"]),
                          "--json", "state"], cwd=repo)
    if code != 0 and "no checks" in (out + err).lower():
        return allow_no_ci, no_ci_msg
    try:
        states = [c.get("state") for c in json.loads(out)]
    except (json.JSONDecodeError, TypeError):
        return False, "could not read checks"
    if not states:
        return allow_no_ci, no_ci_msg
    if all(s == "SUCCESS" for s in states):
        return True, "all checks SUCCESS"
    return False, f"checks not green: {states}"


def pr_state(ctx, ticket, hs):
    """'open' | 'merged' | 'closed'. Used for the stale-approval re-check."""
    if ctx.cfg.FAKE or not ticket["repo_path"]:
        return "merged" if hs.get("merged") else "open"
    repo, err = local_repo(ctx, ticket)
    if err:
        return "open"
    _, out, _ = _run(ctx.cfg, [ctx.cfg.GH_BIN, "pr", "view", str(hs["pr_number"]),
                     "--json", "state"], cwd=repo)
    try:
        return json.loads(out).get("state", "open").lower()
    except (json.JSONDecodeError, TypeError):
        return "open"


def merge_pr(ctx, ticket, hs):
    if ctx.cfg.FAKE or not ticket["repo_path"]:
        hs["merged"] = True
        return True
    repo, err = local_repo(ctx, ticket)
    if err:
        return False
    code, _, _ = _run(ctx.cfg, [ctx.cfg.GH_BIN, "pr", "merge", str(hs["pr_number"]),
                      "--squash"], cwd=repo)
    return code == 0


def cleanup_worktree(ctx, ticket, hs):
    path = hs.get("worktree_path")
    if not path:
        return
    if not ctx.cfg.FAKE and ticket["repo_path"]:
        repo, err = local_repo(ctx, ticket)
        if not err:
            _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", repo,
                           "worktree", "remove", "--force", path])
            if hs.get("branch"):
                _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", repo, "branch", "-D", hs["branch"]])
            _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", repo, "worktree", "prune"])
    shutil.rmtree(path, ignore_errors=True)


def reap_worktrees(ctx):
    """Remove worktrees whose ticket is no longer active/blocked (must-fix #7)."""
    conn = ctx.conn
    for d in config.WORKTREES_DIR.glob("ticket-*"):
        try:
            tid = int(d.name.split("-")[1])
        except (IndexError, ValueError):
            continue
        row = conn.execute("SELECT status FROM ticket WHERE id=?", (tid,)).fetchone()
        if row and row["status"] in ("active", "blocked"):
            continue
        shutil.rmtree(d, ignore_errors=True)
        db.append_audit(conn, "recovery", "worktree_reaped",
                        f"removed orphan worktree {d.name}", ticket_id=tid,
                        tick_id=ctx.tick_id)
