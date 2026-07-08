"""One-time worker warm-up: exercise every privileged operation a ticket performs
(spawn headless claude with Edit/Write/Bash, shell out to git and gh, touch the
data dirs) so macOS fires its permission dialogs — TCC, keychain, network — at
SETUP time, while a human is at the machine, instead of mid-ticket from a
background launchd process.

macOS attaches grants to the responsible process, so the automatic path runs
INSIDE the worker process (worker.py calls maybe_warmup after the first
heartbeat): dialogs answered for a Terminal-run command attach to Terminal.app
and do NOT transfer to launchd's python — exactly why manual testing looks
clean and the first real ticket then prompts. `outerloop warmup` exists for
re-running by hand (it ignores the done-flag)."""

import subprocess
import threading
import uuid

from . import __version__, agent, config

_attempted = False   # at most one attempt per process; the durable flag is settings.json
_lock = threading.Lock()  # combined node: hub scheduler + worker thread both call in


def run_warmup(run=None, real=None):
    """Exercise git, gh and one tiny headless claude run in a scratch dir. Returns
    True when the claude run completed (the flag-setting condition); git/gh are
    best-effort — a missing binary is doctor's problem, not warmup's. `run` and
    `real` are injected so tests need no real binaries."""
    run = run or (lambda argv: subprocess.run(argv, capture_output=True, timeout=30))
    real = real or agent._real
    for argv in ([config.GIT_BIN, "--version"], [config.GH_BIN, "auth", "status"]):
        try:
            run(argv)
        except Exception as e:  # noqa: BLE001 — warmup must never kill the worker
            print(f"warmup: {argv[0]}: {e}")
    config.ensure_dirs()
    scratch = config.WORKTREES_DIR / "warmup"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        res = real(config, "warmup",
                   "This is a one-time permissions warm-up, not a coding task.\n"
                   "1. With the Bash tool run: echo warmup-ok\n"
                   "2. With the Write tool create warmup.txt containing: ok\n"
                   "Then reply with exactly: done",
                   scratch, "Edit,Write,Bash", str(uuid.uuid4()),
                   config.resolve_model("warmup"))
    except Exception as e:  # noqa: BLE001
        print(f"warmup: claude run failed: {e}")
        return False
    if res["timed_out"] or res["exit_code"] != 0:
        print(f"warmup: claude run failed"
              f" (exit={res['exit_code']}, timed_out={res['timed_out']})")
        return False
    return True


def maybe_warmup(run=None, real=None):
    """Worker-startup hook: once per install (settings flag), at most one attempt
    per process, never in FAKE mode, never raises. Called after apply_hub_cfg so
    an inherited real-mode flip triggers it too."""
    global _attempted
    if _attempted or config.FAKE:
        return
    with _lock:
        if _attempted:
            return
        if config.local_setting("warmed_up"):
            _attempted = True  # latch in-process: no settings.json read per poll
            return
        _attempted = True
    print("warmup: first real-mode start — exercising claude/git/gh once so macOS "
          "permission prompts fire now, not mid-ticket")
    if run_warmup(run=run, real=real):
        config.set_local("warmed_up", __version__)
        print("warmup: done")
    else:
        print("warmup: incomplete — will retry on next worker start")
