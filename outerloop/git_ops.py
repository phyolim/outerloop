"""Verify + gate helpers for the coding lifecycle ("agent does, code verifies").

The AGENT performs the environment-sensitive git plumbing inside its stage prompt —
clone, worktree, branch, commit, push, PR-create — because adapting to a messy
environment is what it is good at (subprocess plumbing here spent a week accreting
PATH/URL/exit-code fixes the agent never needed). This module keeps only what must be
DETERMINISTIC:
  - verification that the agent's claimed work really exists (worktree commit, PR),
  - the human-gated actions (repo creation, the merge — the one door to main),
  - merge preconditions (checks_green) and worktree janitorial work.
gh reaches GitHub via `-R owner/repo` for PR state/checks/merge, so gating needs no
local clone at all. All subprocess calls are list-form (never shell=True), so
ticket-controlled text can't inject args or shell. FAKE mode simulates GitHub in
handler_state so the lifecycle runs with no repo."""

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


def normalize_repo_path(repo_path):
    """Canonicalize a human-supplied repo_path at intake (UI/API): repo_path is
    machine-independent ticket state, so a local clone path must be resolved to its
    origin URL BEFORE it is stored — accepted verbatim, it strands every other worker
    and, worse, makes the author's clone destination equal its clone source, so the
    agent just git-inits an origin-less repo the shipper can never push (ticket #8).
    Returns (canonical_value_or_None, error_or_None); empty input is (None, None).
    FAKE mode passes anything through verbatim — nothing there ever touches git."""
    repo_path = (repo_path or "").strip()
    if not repo_path:
        return None, None
    if config.FAKE:
        return repo_path, None
    if _REMOTE_URL_RE.match(repo_path):
        return re.sub(r"\.git$", "", repo_path.rstrip("/")), None
    p = Path(repo_path).expanduser()
    if not p.is_dir():
        return None, (f"repo_path is neither a remote URL nor a directory on this"
                      f" machine: {repo_path} — use the repo's URL")
    code, out, err = _run(None, [config.GIT_BIN, "-C", str(p),
                          "remote", "get-url", "origin"])
    if code != 0 or not out:
        return None, (f"local repo {repo_path} has no origin remote to canonicalize"
                      " — use the repo's URL")
    return re.sub(r"\.git$", "", out.strip().rstrip("/")), None


def gh_slug(ctx, ticket):
    """The 'owner/name' GitHub slug for `gh -R`, derived from the ticket's CANONICAL
    repo_path (a remote URL, or a local path whose origin remote we read). repo_path
    is machine-independent state and is never rewritten to a machine-local clone path:
    in a fleet, whichever worker claims a stage resolves what it needs locally —
    writing one box's clone path into the shared ticket strands every other box (a
    ticket that bounced mbp -> mini failed exactly that way).
    Returns (slug_or_None, error_or_None)."""
    repo_path = ticket["repo_path"]
    if ctx.cfg.FAKE or not repo_path:
        return None, "no repo_path"
    url = repo_path
    if not _REMOTE_URL_RE.match(repo_path):
        if not Path(repo_path).exists():
            return None, (f"repo path does not exist on this machine: {repo_path}"
                          " (use the repo's URL so any worker can resolve it)")
        code, out, err = _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", repo_path,
                              "remote", "get-url", "origin"])
        if code != 0 or not out:
            return None, (err or f"no origin remote in {repo_path}")
        url = out
    m = re.search(r"[:/]([^/:]+/[^/:]+?)(\.git)?/?$", url.strip())
    if not m:
        return None, f"could not parse a GitHub owner/repo out of {url!r}"
    return m.group(1), None


def clone_name(repo_path):
    """Directory name for this repo's clone under REPOS_DIR (from the URL's last
    segment). Deterministic so every stage/machine derives the same location."""
    return re.sub(r"\.git$", "", repo_path.rstrip("/")).rsplit("/", 1)[-1]


def worktree_path(ticket, branch):
    """Deterministic per-ticket worktree location — code picks the path (collision-
    proof, reapable by name) even though the AGENT creates the tree itself."""
    return config.WORKTREES_DIR / f"ticket-{ticket['id']}-{branch.rsplit('-', 1)[-1]}"


def verify_worktree(ctx, ticket, hs):
    """Post-author verification: the worktree the agent was told to create really is a
    git worktree on the right branch with at least one commit. Returns
    (base_branch_or_None, error_or_None) — base is what branch_diff diffs against.
    Trust nothing the agent SAID; check what exists."""
    if ctx.cfg.FAKE or not ticket["repo_path"]:
        return "HEAD", None
    wt = hs.get("worktree_path")
    code, out, err = _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", wt,
                          "rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0:
        return None, f"no git worktree at {wt}: {err or out}"
    if out != hs.get("branch"):
        return None, f"worktree is on branch {out!r}, expected {hs.get('branch')!r}"
    code, base, _ = _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", wt,
                         "rev-parse", "--abbrev-ref", "origin/HEAD"])
    return (base if code == 0 and base else "HEAD"), None


def verify_pr(ctx, ticket, branch):
    """Post-shipper verification: read the PR for `branch` straight from GitHub
    (gh -R — no local clone needed). Returns (number, url, error)."""
    slug, err = gh_slug(ctx, ticket)
    if err:
        return None, "", err
    code, out, err = _run(ctx.cfg, [ctx.cfg.GH_BIN, "pr", "view", branch,
                          "-R", slug, "--json", "number,url"])
    if code != 0:
        return None, "", (err or out or f"no PR found for branch {branch}")
    try:
        data = json.loads(out)
        return data.get("number"), data.get("url", ""), None
    except (json.JSONDecodeError, TypeError):
        return None, "", "could not parse gh pr view output"


def branch_diff(ctx, ticket, hs):
    """The branch's full diff vs its base (what a PR would show), read from the local
    worktree so the review/fix loop runs before the PR is opened."""
    if ctx.cfg.FAKE or not ticket["repo_path"]:
        return "diff --git a/src/feature.py b/src/feature.py\n+ fake change\n"
    base = hs.get("base_branch") or "HEAD"
    _, out, _ = _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", hs["worktree_path"],
                     "diff", f"{base}...HEAD"])
    return out


def commit_all(ctx, ticket, hs, message):
    """Backstop commit for whatever the agent left uncommitted in the worktree. The
    agent is TOLD to commit its own work; this catches a forgotten commit so an
    expensive author run is never thrown away over it. No-op in FAKE or when clean."""
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
    slug, rerr = gh_slug(ctx, ticket)
    if rerr:
        return False, f"could not resolve repo: {rerr}"
    code, out, err = _run(ctx.cfg, [ctx.cfg.GH_BIN, "pr", "checks", str(hs["pr_number"]),
                          "-R", slug, "--json", "state"])
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
    slug, err = gh_slug(ctx, ticket)
    if err:
        return "open"
    _, out, _ = _run(ctx.cfg, [ctx.cfg.GH_BIN, "pr", "view", str(hs["pr_number"]),
                     "-R", slug, "--json", "state"])
    try:
        return json.loads(out).get("state", "open").lower()
    except (json.JSONDecodeError, TypeError):
        return "open"


def merge_pr(ctx, ticket, hs):
    """THE gated action — the only path to main, run by the orchestrator strictly
    after human approval (never by an agent)."""
    if ctx.cfg.FAKE or not ticket["repo_path"]:
        hs["merged"] = True
        return True
    slug, err = gh_slug(ctx, ticket)
    if err:
        return False
    code, _, _ = _run(ctx.cfg, [ctx.cfg.GH_BIN, "pr", "merge", str(hs["pr_number"]),
                      "-R", slug, "--squash"])
    return code == 0


def cleanup_worktree(ctx, ticket, hs):
    """Janitorial: drop the worktree dir and, when the parent clone is on this
    machine, detach the worktree registration + throwaway branch. Best-effort — the
    clone may live on another worker; rmtree is the part that must happen here."""
    path = hs.get("worktree_path")
    if not path:
        return
    if not ctx.cfg.FAKE and ticket["repo_path"]:
        code, repo, _ = _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", path,
                             "rev-parse", "--path-format=absolute", "--git-common-dir"])
        if code == 0 and repo.endswith("/.git"):
            repo = repo[:-len("/.git")]
            _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", repo,
                           "worktree", "remove", "--force", path])
            if hs.get("branch"):
                _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", repo, "branch", "-D", hs["branch"]])
            _run(ctx.cfg, [ctx.cfg.GIT_BIN, "-C", repo, "worktree", "prune"])
    shutil.rmtree(path, ignore_errors=True)


def reap_worktrees(ctx):
    """Remove worktrees whose ticket is no longer live (must-fix #7). Live includes
    parked-with-sub_stage (paused/terminated mid-flight): those are resumable in
    place, so their workspace must survive until they're resumed, revived, or closed."""
    conn = ctx.conn
    for d in config.WORKTREES_DIR.glob("ticket-*"):
        try:
            tid = int(d.name.split("-")[1])
        except (IndexError, ValueError):
            continue
        row = conn.execute("SELECT status, sub_stage FROM ticket WHERE id=?",
                           (tid,)).fetchone()
        if row and (row["status"] in ("active", "blocked")
                    or (row["status"] == "parked" and row["sub_stage"])):
            continue
        shutil.rmtree(d, ignore_errors=True)
        db.append_audit(conn, "recovery", "worktree_reaped",
                        f"removed orphan worktree {d.name}", ticket_id=tid,
                        tick_id=ctx.tick_id)
