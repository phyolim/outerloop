# Self-contained: reap_worktrees retires the WHOLE worktree — dir, clone
# registration, and throwaway branch — for tickets in a non-resumable state, while
# sparing failed-with-sub_stage tickets (human Retry re-enters that stage and needs
# the workspace). Real git against throwaway repos; no network, no deps.
import os, sys, atexit, shutil, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-reap-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

import json
from outerloop import config, db, git_ops
from outerloop.context import Ctx

db.init_db()
config.ensure_dirs()


def git(*args, cwd):
    return subprocess.run([config.GIT_BIN, "-c", "user.email=t@t", "-c", "user.name=t",
                           *args], cwd=str(cwd), capture_output=True, text=True)


# A parent clone with one commit, plus a ticket worktree on a minted branch.
clone = config.REPOS_DIR / "demo"
clone.mkdir(parents=True)
git("init", "-b", "main", cwd=clone)
(clone / "f.txt").write_text("x")
git("add", "-A", cwd=clone)
git("commit", "-m", "init", cwd=clone)
wt1 = config.WORKTREES_DIR / "ticket-1-abc12345"
git("worktree", "add", str(wt1), "-b", "outerloop/ticket-1-abc12345", cwd=clone)
assert wt1.exists()

conn = db.connect()
hs1 = json.dumps({"branch": "outerloop/ticket-1-abc12345", "worktree_path": str(wt1)})
conn.execute("INSERT INTO ticket(title, body, type, status, sub_stage, handler_state)"
             " VALUES('done ticket','b','coding','done','done',?)", (hs1,))
conn.execute("INSERT INTO ticket(title, body, type, status, sub_stage, handler_state)"
             " VALUES('failed mid-flight','b','coding','failed','fixing','{}')")

# ticket 2: failed WITH sub_stage -> workspace must survive for Retry
wt2 = config.WORKTREES_DIR / "ticket-2-def"
wt2.mkdir()
# no ticket row at all -> orphan dir, reaped
wt99 = config.WORKTREES_DIR / "ticket-99-zzz"
wt99.mkdir()

git_ops.reap_worktrees(Ctx(conn, config, "tick-test"))

# done ticket: dir gone, branch gone, registration pruned
assert not wt1.exists(), "done ticket's worktree dir must be removed"
branches = git("branch", "--list", "outerloop/ticket-1-abc12345", cwd=clone).stdout
assert not branches.strip(), f"throwaway branch must be deleted, got {branches!r}"
wtl = git("worktree", "list", cwd=clone).stdout
assert "ticket-1" not in wtl, f"worktree registration must be pruned, got {wtl!r}"
print("OK: done ticket fully retired (dir + branch + registration)")

assert wt2.exists(), "failed-with-sub_stage worktree must survive for human Retry"
print("OK: failed mid-flight ticket keeps its workspace")

assert not wt99.exists(), "orphan worktree dir (no ticket row) must be reaped"
print("OK: orphan dir reaped")

# Dismissing the failure (failed -> done, what web _dismiss does) makes it reapable.
conn.execute("UPDATE ticket SET status='done' WHERE id=2")
git_ops.reap_worktrees(Ctx(conn, config, "tick-test"))
assert not wt2.exists(), "dismissed ticket's worktree must now be reaped"
print("OK: dismiss/close ends retention — growth stays bounded")
conn.close()
print("PASSED test_reap")
