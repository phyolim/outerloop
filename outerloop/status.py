"""`outerloop status`: runtime state (complements doctor's prereq checks). Role + mode,
whether the background daemon is running, and — for a worker — hub reachability. Read-only
and fast; every probe has a short timeout so it never hangs."""

import subprocess
import urllib.request

from . import config


def _daemon_running():
    """Is the background service up? `brew services list` is the brew path; launchctl
    print is the .pkg/launchd path. Either 'yes' is enough. Short timeouts, never hang."""
    try:
        p = subprocess.run(["brew", "services", "list"], capture_output=True, text=True, timeout=5)
        for line in p.stdout.splitlines():
            f = line.split()
            if f and f[0] == "outerloop":
                return f[1] in ("started", "running")
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    try:
        import os
        p = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/homebrew.mxcl.outerloop"],
                           capture_output=True, text=True, timeout=5)
        return p.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def _hub_reachable(hub_url):
    """Short-timeout GET to the hub root. True/False; never raises, never hangs."""
    try:
        with urllib.request.urlopen(hub_url.rstrip("/") + "/", timeout=3) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def run_status():
    role = config.local_setting("role", "hub")
    hub_url = config.local_setting("hub_url", "")
    mode = "FAKE" if config.FAKE else "real"
    print(f"role: {role}   mode: {mode}" + (f"   hub_url: {hub_url}" if hub_url else ""))
    print(f"daemon: {'running' if _daemon_running() else 'not running'}")
    if role != "worker":
        # Surface the board lock: an exposed hub self-generates a ui_token and announces it
        # only in the service log — this is the discoverable place. A local shell can read
        # the DB anyway, so printing it here exposes nothing new.
        try:
            from . import db
            conn = db.connect()
            pw = db.get_setting(conn, "ui_token", "")
            api = db.get_setting(conn, "require_auth", "off")
            conn.close()
            print(f"dashboard password: {pw or '(none — board is open)'}   api auth: {api}")
        except Exception:
            pass  # no DB yet (fresh box) — nothing to report
    if role in ("worker", "both"):
        # A worker points at a remote hub; a combined node's co-located worker talks to its
        # own hub over loopback (no hub_url set), so fall back to that.
        url = hub_url or (f"http://127.0.0.1:{config.local_setting('port', 8765)}"
                          if role == "both" else "")
        if url:
            print(f"worker->hub: {'reachable' if _hub_reachable(url) else 'unreachable'} ({url})")
