"""Push notification when the decision queue gains an item — the signal that makes
"away mode" work (the phone buzzes instead of you polling the UI). ntfy-style: one
POST to a URL you own (self-hosted ntfy on the relay VPS, or ntfy.sh/<topic>).
Best-effort by design: a dead notify target must never block or fail a gate write.

Configure: `python3 -m outerloop config notify_url https://ntfy.example.com/inbox`
(or the OUTERLOOP_NOTIFY_URL env var on the hub; empty = off, the default)."""

import os
import threading
import urllib.request

from . import db


def _target(conn):
    return os.environ.get("OUTERLOOP_NOTIFY_URL") or db.get_setting(conn, "notify_url", "")


def _post(url, title, message):
    try:
        # Request() itself raises on a malformed/scheme-less URL (e.g. "ntfy.sh/inbox"
        # set without https://) — keep it inside the guard so a misconfigured target
        # degrades to silence, not a traceback per decision.
        req = urllib.request.Request(
            url, data=message.encode(),
            headers={"Title": title.encode("ascii", "replace").decode(),
                     "Content-Type": "text/plain; charset=utf-8"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass  # best-effort: never let a notification failure surface


def send(conn, title, message):
    """Fire-and-forget. Reads the target on the caller's conn (cheap), then POSTs
    off-thread so a slow/dead notify host can't stall the hub scheduler. NON-daemon
    on purpose: a short-lived `outerloop tick` process must outlive the POST (a daemon
    thread would be killed at interpreter exit before the send); the 5s socket
    timeout in _post bounds how long it can hold the process open."""
    url = _target(conn)
    if not url:
        return
    threading.Thread(target=_post, args=(url, title, message), daemon=False).start()
