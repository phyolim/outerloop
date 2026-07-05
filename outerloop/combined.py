"""role=both: run the hub AND a co-located worker in one process (one brew service).

The single-box combined node — this Mac is the fleet hub and also does work itself. The
hub runs in the main thread; the worker runs in a daemon thread pointed at the hub over
loopback, going through the exact same HTTP seam a remote worker uses (epoch-fenced
/api/op), so there is no second code path. On a hardened hub (auth on) the co-located
worker needs a token like any other machine; we mint and provision one here on first run,
so `outerloop local role both` + a restart is all it takes — no manual token step."""

import json
import secrets
import socket
import threading
import time

from . import auth, config, db
from .hub import run_hub
from .worker import run_worker

# The seed default lives in config.DEFAULT_CAPS (shared with LAN pairing).
DEFAULT_CAPS = config.DEFAULT_CAPS


def _ensure_local_identity(conn):
    """Give this box a stable worker name + a token that resolves to it, so the co-located
    worker can authenticate to its own hub once auth is on (a LAN-bound hub turns it on).
    Idempotent: reuses an existing name/token, minting only when missing or mismatched, and
    seeds capabilities the first time so the worker is actually eligible for work."""
    name = config.local_setting("worker") or socket.gethostname().split(".")[0] or "worker"
    if not config.local_setting("worker"):
        config.set_local("worker", name)
    # Always ensure a matching token: a 0.0.0.0 bind hardens the hub to auth-on *after* this
    # runs, so we can't gate on the current auth state — provision unconditionally (a token
    # is harmless while auth is off, required once it flips on).
    tok = config.local_setting("token")
    if not tok or auth.resolve(conn, tok) != name:
        tok = secrets.token_hex(24)
        auth.set_token(conn, name, tok)   # upserts the worker row
        config.set_local("token", tok)
        print(f"[outerloop] provisioned local worker '{name}' for the co-located worker", flush=True)
    row = conn.execute("SELECT capabilities FROM worker WHERE name=?", (name,)).fetchone()
    if not (row and row["capabilities"] and json.loads(row["capabilities"])):
        caps = json.loads(config.local_setting("caps") or "null") or DEFAULT_CAPS
        with db.immediate(conn):
            conn.execute("UPDATE worker SET capabilities=? WHERE name=?", (json.dumps(caps), name))
    return name


def _worker_supervisor(base):
    """Keep the co-located worker alive without ever taking the hub down with it. The
    worker's own loop already backs off on hub errors; this only catches the unexpected so a
    stray exception restarts the worker thread instead of the whole process. Self-update is
    off here: the hub IS the update source on this box, and a worker exit would stop the hub
    too — a combined node updates when its hub install does."""
    while True:
        try:
            run_worker(base=base, self_update=False)
            return  # a clean return means no hub configured; nothing to supervise
        except Exception as e:  # noqa: BLE001 — the worker thread must never kill the hub
            print(f"[outerloop] co-located worker crashed ({e}); restarting in 5s", flush=True)
            time.sleep(5)


def run_combined():
    bind = config.local_setting("bind", "0.0.0.0")
    port = int(config.local_setting("port", 8765))
    conn = db.init_db()
    name = _ensure_local_identity(conn)
    conn.close()
    base = f"http://127.0.0.1:{port}"
    print(f"combined node: hub on {bind}:{port} + co-located worker '{name}' -> {base}", flush=True)
    # Worker first (daemon so it dies with the hub); it backs off until the hub is listening.
    threading.Thread(target=_worker_supervisor, args=(base,), daemon=True).start()
    run_hub(host=bind, port=port)   # blocks in the main thread
