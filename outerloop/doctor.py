"""`outerloop doctor`: the real-mode prereq check from deploy/mac/scripts/preflight.sh,
as testable Python. Checks python>=3.9, git present + commit identity set, gh present +
authed, claude resolvable — and returns [{name, ok, detail, fix}]. run() is injected so
tests can stub present/absent binaries without a real gh/claude on the box."""

import shutil
import subprocess
import sys

from . import config


def _run(argv):
    """Default runner: returns (rc, stdout). Missing binary -> rc 127 (never raises)."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return p.returncode, p.stdout
    except (FileNotFoundError, OSError):
        return 127, ""
    except subprocess.TimeoutExpired:
        return 1, ""


def checks(run=_run, which=shutil.which):
    """Real-mode prereqs as a structured list. `run(argv)->(rc,out)` and `which(name)`
    are injected so tests can simulate present/absent binaries and git identity."""
    out = []

    ok = sys.version_info[:2] >= (3, 9)
    out.append({"name": "python ≥3.9", "ok": ok,
                "detail": ".".join(map(str, sys.version_info[:3])),
                "fix": "install python ≥3.9 (brew install python)"})

    git = which("git")
    if git:
        email, _ = run([git, "config", "--get", "user.email"])[1], None
        name = run([git, "config", "--get", "user.name"])[1].strip()
        ident = bool(email.strip()) and bool(name)
        out.append({"name": "git", "ok": True, "detail": git, "fix": ""})
        out.append({"name": "git commit identity", "ok": ident,
                    "detail": "set" if ident else "user.email/user.name unset",
                    "fix": "git config --global user.email you@example.com  (and user.name)"})
    else:
        out.append({"name": "git", "ok": False, "detail": "not found",
                    "fix": "install Xcode CLT / git"})

    gh = which("gh")
    if gh:
        out.append({"name": "gh", "ok": True, "detail": gh, "fix": ""})
        authed = run([gh, "auth", "status"])[0] == 0
        out.append({"name": "gh authenticated", "ok": authed,
                    "detail": "ok" if authed else "not authenticated", "fix": "gh auth login"})
    else:
        out.append({"name": "gh", "ok": False, "detail": "not found", "fix": "brew install gh"})

    claude = which("claude") or (config.CLAUDE_BIN if which(config.CLAUDE_BIN) else None)
    out.append({"name": "claude", "ok": bool(claude),
                "detail": claude or "not found",
                "fix": "install the Claude Code CLI (required for real mode)"})
    return out


def run_doctor(run=_run):
    """Print ✓/✗ lines mirroring preflight.sh, a FAKE/real + role/hub_url footer, and a
    summary. Exit non-zero if any real-mode blocker is present (so scripts can gate)."""
    results = checks(run=run)
    for c in results:
        mark = "✓" if c["ok"] else "✗"
        print(f"  {mark} {c['name']}: {c['detail']}")
        if not c["ok"] and c["fix"]:
            print(f"      ↳ {c['fix']}")

    role = config.local_setting("role", "hub")
    hub_url = config.local_setting("hub_url", "")
    mode = "FAKE" if config.FAKE else "real"
    print()
    print(f"mode: {mode}   role: {role}" + (f"   hub_url: {hub_url}" if hub_url else ""))

    fails = sum(1 for c in results if not c["ok"])
    if fails:
        print(f"doctor: {fails} real-mode blocker(s) — real mode will fail until fixed")
        return 1
    print("doctor: real-mode prereqs OK")
    return 0
