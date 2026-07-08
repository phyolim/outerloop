"""warmup.maybe_warmup guards + flag logic with a stubbed runner and agent — no real
binaries or claude. FAKE-safe, throwaway."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-warmup-")
os.environ["OUTERLOOP_FAKE"] = "1"

from outerloop import config, warmup

calls = []


def fake_run(argv):
    calls.append(("run", argv[0]))


def real_ok(cfg, role, prompt, cwd, allowed_tools, session_id, model, on_event=None):
    calls.append(("claude", role))
    assert role == "warmup" and "Bash" in allowed_tools
    assert model, "warmup must pass a resolved model to _real"
    return {"exit_code": 0, "timed_out": False}


def real_fail(cfg, role, prompt, cwd, allowed_tools, session_id, model, on_event=None):
    calls.append(("claude", role))
    return {"exit_code": 1, "timed_out": False}


# --- FAKE mode: maybe_warmup is a no-op ---
warmup.maybe_warmup(run=fake_run, real=real_ok)
assert not calls, "FAKE mode must skip warmup entirely"
assert not warmup._attempted, "a FAKE skip must not consume the per-process attempt"

# --- real mode, claude fails: no flag, retries on next process start ---
config.FAKE = False
warmup.maybe_warmup(run=fake_run, real=real_fail)
assert ("claude", "warmup") in calls, "real mode must attempt the claude run"
assert not config.local_setting("warmed_up"), "failed warmup must not set the flag"
warmup.maybe_warmup(run=fake_run, real=real_ok)
assert calls.count(("claude", "warmup")) == 1, "one attempt per process, even after failure"

# --- simulated restart: succeeds, sets the flag, then never runs again ---
warmup._attempted = False
warmup.maybe_warmup(run=fake_run, real=real_ok)
assert config.local_setting("warmed_up"), "successful warmup must persist the flag"
assert (config.WORKTREES_DIR / "warmup").is_dir(), "scratch dir must exist for cwd"
warmup._attempted = False
calls.clear()
warmup.maybe_warmup(run=fake_run, real=real_ok)
assert not calls, "warmed_up flag must make later starts a no-op"

# --- run_warmup: a raising git/gh runner is best-effort, claude still gates ---
def run_boom(argv):
    raise FileNotFoundError(argv[0])

assert warmup.run_warmup(run=run_boom, real=real_ok), "missing git/gh must not fail warmup"
assert not warmup.run_warmup(run=fake_run, real=real_fail), "claude failure must fail warmup"

print("test_warmup: OK")
