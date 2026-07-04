"""The hub. One HTTP server exposes the JSON API (/api/*) and the web UI (everything
else). A background scheduler thread runs the cheap, DB-only top-half of the old tick
on a timer: reclaim expired leases, triage junk, score, and resume answered decisions.
Handlers and agents run on WORKERS, not here."""

import io
import json
import os
import tarfile
import threading
import uuid
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from . import api, auth, config, db, gate, leasing, scoring, tick, triage
from . import __file__ as _pkg_init
from .context import Ctx
from .handlers import get_handler
from .web import Handler as WebHandler


# App root = the dir containing the inbox/ package. Derived, never hardcoded.
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(_pkg_init)))
_EXCLUDE_DIRS = {"data", "__pycache__", ".git", ".claude", "dist", ".venv", "venv",
                 "node_modules"}
_EXCLUDE_FILES = {"deploy.env", "settings.json"}


def _excluded(rel):
    """rel is a path RELATIVE to the app root. Drop runtime state and token-carrying
    artifacts (same spirit as the rsync excludes in build-pkg.sh)."""
    parts = rel.split("/")
    if _EXCLUDE_DIRS.intersection(parts):
        return True
    name = parts[-1]
    return name in _EXCLUDE_FILES or name.endswith(".pkg")


def _build_update_tar():
    """gzip tarball of the app tree, member names relative to _APP_ROOT, in memory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for dirpath, dirnames, filenames in os.walk(_APP_ROOT):
            rel_dir = os.path.relpath(dirpath, _APP_ROOT)
            rel_dir = "" if rel_dir == "." else rel_dir
            # Prune excluded dirs so os.walk doesn't descend into them.
            dirnames[:] = [d for d in dirnames
                           if not _excluded(f"{rel_dir}/{d}".lstrip("/"))]
            for f in filenames:
                rel = f"{rel_dir}/{f}".lstrip("/")
                if _excluded(rel):
                    continue
                tar.add(os.path.join(dirpath, f), arcname=rel, recursive=False)
    return buf.getvalue()


class CoordHandler(WebHandler):
    """The web UI handler, plus an /api/* JSON branch in front of it."""

    def do_GET(self):
        # /api/update streams a gzip tarball, so it can't go through _api (which only
        # returns JSON tuples). Handle it before the generic /api/* branch.
        if urlparse(self.path).path == "/api/update":
            return self._update()
        if self.path.startswith("/api/"):
            return self._api("GET")
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._api("POST")
        return super().do_POST()

    def _api(self, method):
        body = None
        if method == "POST":
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                return self._json(400, {"error": "bad json"})
        h = self.headers.get("Authorization", "")
        token = h[7:] if h.startswith("Bearer ") else None
        u = urlparse(self.path)
        path = u.path
        conn = db.connect()
        auth_device = None
        try:
            auth_device = auth.resolve(conn, token)
            if path == "/api/intake":
                # Intake self-authenticates against settings.intake_token (webhook senders
                # have no device token). Accept it as Bearer or ?token= (header-less senders).
                from urllib.parse import parse_qs
                supplied = token or parse_qs(u.query).get("token", [None])[0]
                status, obj = api.handle(method, path, body, conn, intake_token=supplied)
            elif auth.required(conn) and auth_device is None:
                status, obj = 401, {"error": "authentication required"}
            else:
                status, obj = api.handle(method, path, body, conn, auth_device)
        except Exception as e:  # an API bug must not take the hub down
            status, obj = 500, {"error": str(e)}
        try:
            db.log_request(conn, auth_device or (body or {}).get("device"), method, path, status)
        except Exception:
            pass  # request logging must never break a request
        conn.close()
        self._json(status, obj)

    def _json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _update(self):
        """GET /api/update: gzip tarball of the hub's own app tree. Auth-gated exactly
        like _api (bearer token + require_auth), logged the same way; the update channel
        must be no more open than the JSON API."""
        h = self.headers.get("Authorization", "")
        token = h[7:] if h.startswith("Bearer ") else None
        conn = db.connect()
        auth_device = None
        try:
            auth_device = auth.resolve(conn, token)
            if auth.required(conn) and auth_device is None:
                try:
                    db.log_request(conn, None, "GET", "/api/update", 401)
                except Exception:
                    pass  # request logging must never break a request
                conn.close()
                return self._json(401, {"error": "authentication required"})
            try:
                db.log_request(conn, auth_device, "GET", "/api/update", 200)
            except Exception:
                pass
        finally:
            conn.close()
        data = _build_update_tar()
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _resume_answered(ctx):
    """Apply human decisions on the hub (the gate stays hub-central). A worker can
    never resume a gated ticket — it only claims tickets with no pending decision."""
    conn = ctx.conn
    for t in conn.execute("SELECT * FROM ticket WHERE status='active'"
                          " AND blocked_by_decision_id IS NOT NULL").fetchall():
        dec = gate.answered_decision(conn, t)
        if dec:
            tick._resume(ctx, t, dec, get_handler(t["type"]))


def scheduler_once():
    conn = db.connect()
    try:
        if db.get_setting(conn, "kill_switch", "off") == "on" or config.KILL_FILE.exists():
            return
        ctx = Ctx(conn, config, "sched-" + uuid.uuid4().hex[:8])
        leasing.reclaim_fleet(conn, ctx.tick_id)
        leasing.park_stranded(conn, ctx.tick_id)
        triage.triage_new(ctx)
        scoring.score_unscored(ctx)
        _resume_answered(ctx)
    finally:
        conn.close()


def _scheduler_loop(stop):
    while not stop.is_set():
        try:
            scheduler_once()
        except Exception as e:
            print("[scheduler] error:", e)
        stop.wait(config.SCHED_INTERVAL_SEC)


def run_coordinator(host=None, port=8765):
    db.init_db()
    host = host or "127.0.0.1"
    if not auth.is_safe_bind(host) and os.environ.get("INBOX_ALLOW_PUBLIC_BIND") != "1":
        raise SystemExit(
            f"refusing to bind {host!r}: not loopback, a private-LAN address, or 0.0.0.0. "
            "Use --lan (binds 0.0.0.0 for the LAN) or a private address; set "
            "INBOX_ALLOW_PUBLIC_BIND=1 only to bind a routable public IP.")
    stop = threading.Event()
    threading.Thread(target=_scheduler_loop, args=(stop,), daemon=True).start()
    srv = ThreadingHTTPServer((host, port), CoordHandler)
    print(f"coordinator on http://{host}:{port}  (API /api/*, UI /)  FAKE={config.FAKE}")
    try:
        srv.serve_forever()
    finally:
        stop.set()
