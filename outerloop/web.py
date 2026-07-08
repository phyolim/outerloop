"""The dashboard server: serves the built React SPA (ui/dist) with a history-API
fallback — /fleet, /parked, /log, /decisions, /ticket/N all deep-link into the
app — plus the /ui/* JSON seam it reads and writes through. Read-mostly with
small single-row writes, so it shares the SQLite file with the cron worker via
WAL without contention."""

import hmac
import json
import mimetypes
import re
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, auth, config, db, git_ops, pairing, scoring, taxonomy


def _ctx_public(ctx):
    """Whitelist the decision-context fields the SPA renders (PR, diff, checks,
    findings) — never dumps internal drafted payloads."""
    keys = ("pr_url", "diff_stat", "checks", "checks_green", "findings")
    return {k: ctx[k] for k in keys if k in ctx}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, code=200, ctype="text/html; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_send(self, obj, code=200):
        self._send(json.dumps(obj), code=code, ctype="application/json")

    def _events(self):
        """SSE stream. Watches SQLite's PRAGMA data_version (bumps on any write from
        another connection — request handlers or the worker) on a dedicated connection
        and pushes a 'change' event so the client refetches. Replaces client polling."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        conn = db.connect()
        try:
            last = conn.execute("PRAGMA data_version").fetchone()[0]
            self.wfile.write(b"retry: 3000\ndata: hello\n\n")
            self.wfile.flush()
            idle = 0
            while True:
                time.sleep(1)
                ver = conn.execute("PRAGMA data_version").fetchone()[0]
                if ver != last:
                    last = ver
                    idle = 0
                    self.wfile.write(b"data: change\n\n")
                else:
                    idle += 1
                    if idle < 15:
                        continue
                    idle = 0
                    self.wfile.write(b": keepalive\n\n")  # comment frame; keeps proxies/EventSource alive
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client disconnected
        finally:
            conn.close()

    def _serve_static(self, rel):
        """Serve a built SPA asset from ui/dist (path-traversal guarded). Returns True
        if it handled the request, False if there's no such file (fall through)."""
        root = config.UI_DIST.resolve()
        target = (root / rel.lstrip("/")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return False
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(target.read_bytes(), ctype=ctype)
        return True

    def _form(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode()
        return {k: v[0] for k, v in parse_qs(raw).items()}

    # -- dashboard password gate ---------------------------------------------
    # When settings.ui_token is set, the human dashboard (/ and /ui/*) requires it,
    # carried as an ol_ui cookie holding the secret's hash. Empty ui_token = open
    # (loopback/trusted-LAN default; nothing breaks on existing installs). This is
    # the customer-owned lock in the SNI-passthrough gateway model — the hub, not the
    # relay, is what authenticates the board. Worker API (/api/*) is gated separately
    # in HubHandler and never reaches this handler.
    def _ui_secret(self):
        conn = db.connect()
        try:
            return db.get_setting(conn, "ui_token", "") or ""
        finally:
            conn.close()

    def _ui_cookie(self, secret):
        # Store the hash, not the raw secret, in the browser jar. No Secure flag so it
        # also works over http loopback; over the gateway the whole leg is TLS anyway.
        # ponytail: HttpOnly+SameSite=Strict is enough here; add Secure if ever served plaintext on an untrusted net.
        return f"ol_ui={auth.hash_token(secret)}; Path=/; HttpOnly; SameSite=Strict"

    def _cookie_ok(self, secret):
        want = auth.hash_token(secret)
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "ol_ui" and hmac.compare_digest(v, want):
                return True
        return False

    def _login_form(self, code=200, error=False):
        err = '<p style="color:#f87171;margin:0">wrong password</p>' if error else ''
        self._send(
            "<!doctype html><meta charset=utf-8><title>outerloop</title>"
            "<style>body{font:14px system-ui;display:grid;place-items:center;height:100vh;"
            "margin:0;background:#0b0b0c;color:#e6e6e6}form{display:grid;gap:10px;width:260px}"
            "input{padding:8px;border-radius:6px;border:1px solid #333;background:#151517;color:#eee}"
            "button{padding:8px;border-radius:6px;border:0;background:#4f46e5;color:#fff;cursor:pointer}"
            "</style><form method=post action=/ui/login>" + err +
            "<input type=password name=secret placeholder='dashboard password' autofocus>"
            "<button>Unlock</button></form>", code=code)

    def _gate(self, u):
        """True to proceed; False if this method already sent the response."""
        secret = self._ui_secret()
        if not secret:
            return True  # gate disabled
        if self.command == "POST" and u.path == "/ui/login":
            if hmac.compare_digest(self._form().get("secret", ""), secret):
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", self._ui_cookie(secret))
                self.end_headers()
            else:
                self._login_form(error=True)
            return False
        if self._cookie_ok(secret):
            return True
        if self.command == "POST":
            self._form()  # drain the request body so keep-alive stays in sync
        self._login_form(code=401)
        return False

    # -- routing --------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        if not self._gate(u):
            return
        if u.path == "/ui/events":
            return self._events()
        if u.path.startswith("/ui/"):
            conn = db.connect()
            try:
                if u.path == "/ui/board.json":
                    project = parse_qs(u.query).get("project", [None])[0] or None
                    self._json_send(self._board_json(conn, project))
                elif u.path == "/ui/done.json":
                    project = parse_qs(u.query).get("project", [None])[0] or None
                    self._json_send(self._done_json(conn, project))
                elif u.path == "/ui/decisions.json":
                    self._json_send(self._decisions_json(conn))
                elif u.path == "/ui/inbox.json":
                    self._json_send(self._inbox_json(conn))
                elif u.path == "/ui/tickets.json":
                    project = parse_qs(u.query).get("project", [None])[0] or None
                    self._json_send(self._tickets_json(conn, project))
                elif u.path == "/ui/fleet.json":
                    self._json_send(self._fleet_json(conn))
                elif u.path == "/ui/parked.json":
                    self._json_send(self._parked_json(conn))
                elif u.path == "/ui/log.json":
                    self._json_send(self._log_json(conn))
                elif u.path == "/ui/requests.json":
                    self._json_send(self._requests_json(conn))
                elif u.path == "/ui/search.json":
                    q = parse_qs(u.query).get("q", [""])[0]
                    self._json_send(self._search_json(conn, q))
                elif u.path == "/ui/pair.json":
                    self._json_send({"requests": pairing.pending(),
                                     "seed_caps": config.DEFAULT_CAPS})
                elif u.path == "/ui/insights.json":
                    self._json_send(self._insights_json(conn))
                elif u.path == "/ui/ticket.json":
                    tid = parse_qs(u.query).get("id", [None])[0]
                    data = self._ticket_json(conn, int(tid)) if tid and tid.isdigit() else None
                    self._json_send(data or {"error": "not found"}, 200 if data else 404)
                else:
                    self._json_send({"error": "not found"}, 404)
            finally:
                conn.close()
            return
        # Everything that isn't the JSON seam is the React SPA. Real files (hashed
        # assets, favicon) are served as-is; every other path gets index.html — the
        # history-API fallback that lets /fleet, /parked, /log, /decisions, /ticket/N
        # deep-link straight into the app (the routes the server-rendered UI used to own).
        if config.UI_DIST.is_dir():
            if u.path != "/" and self._serve_static(u.path):
                return
            return self._send((config.UI_DIST / "index.html").read_bytes())
        # No built UI on this install: say so instead of pretending. The JSON API above
        # still works, so a rebuilt/full install can be verified with curl either way.
        self._send(
            "<!doctype html><meta charset=utf-8><title>outerloop</title>"
            "<p>UI not built. Install the full release (brew) or run "
            "<code>cd ui && npm ci && npm run build</code>, then reload.</p>")

    def do_POST(self):
        # All writes go through the /ui/* JSON seam the SPA uses (the legacy form
        # endpoints were the server-rendered UI's, which is gone).
        u = urlparse(self.path)
        if not self._gate(u):
            return
        if not u.path.startswith("/ui/"):
            self._form()  # drain the body so keep-alive stays in sync
            return self._json_send({"error": "not found"}, 404)
        conn = db.connect()
        try:
            return self._ui_post(conn, u.path)
        finally:
            conn.close()

    # -- write helpers --------------------------------------------------------
    def _answer(self, conn, f):
        """Answer a pending decision. Returns False (writing nothing) when the decision
        is unknown or already answered — a stale/double answer must not re-activate a
        ticket that has since moved on, nor log a phantom audit row."""
        did = int(f["decision_id"])
        action = f.get("action")
        # 'rework' = neither go nor stop: reject the gated action but hand the note back
        # to the worker for another pass (stored as rejected+rework=1; see db._migrate).
        status = "approved" if action == "approve" else "rejected"
        rework = 1 if action == "rework" else 0
        d = conn.execute("SELECT ticket_id FROM decision WHERE id=?", (did,)).fetchone()
        if d is None:
            return False
        with db.immediate(conn):
            cur = conn.execute("UPDATE decision SET status=?, rework=?, answer_note=?,"
                               " answered_at=datetime('now')"
                               " WHERE id=? AND status='pending'", (status, rework, f.get("note", ""), did))
            if cur.rowcount == 0:  # no longer pending — no-op
                return False
            conn.execute("UPDATE ticket SET status='active', updated_at=datetime('now') WHERE id=?",
                         (d["ticket_id"],))
            db.append_audit(conn, "human", "answered",
                            f"{'rework' if rework else status}: {f.get('note','')}",
                            ticket_id=d["ticket_id"], detail={"decision_id": did})
        return True

    def _start(self, conn, tid):
        # Submit a draft: the next triage pass (tick / hub scheduler) picks it up.
        with db.immediate(conn):
            n = conn.execute("UPDATE ticket SET draft=0, updated_at=datetime('now')"
                             " WHERE id=? AND status='inbox' AND draft=1", (tid,)).rowcount
            if n:
                db.append_audit(conn, "human", "started", "draft submitted to the pipeline",
                                ticket_id=tid)
        return n

    def _retry(self, conn, f):
        # A failed ticket goes back to 'active' at its current sub_stage; the next tick
        # re-enters that stage. Reset the stall counter so it isn't re-failed immediately.
        tid = int(f["ticket_id"])
        with db.immediate(conn):
            conn.execute("UPDATE ticket SET status='active', attempts=0,"
                         " updated_at=datetime('now') WHERE id=? AND status='failed'", (tid,))
            db.append_audit(conn, "human", "retried", "human retried a failed ticket",
                            ticket_id=tid)

    def _dismiss(self, conn, f):
        tid = int(f["ticket_id"])
        with db.immediate(conn):
            conn.execute("UPDATE ticket SET status='done', updated_at=datetime('now')"
                         " WHERE id=? AND status='failed'", (tid,))
            db.append_audit(conn, "human", "dismissed", "human dismissed a failed ticket",
                            ticket_id=tid)

    def _close(self, conn, f):
        """Close a ticket that's no longer relevant — any state except done. Fences an
        in-flight worker (claim_epoch bump -> its next write is stale and abandoned),
        drops the lease, and voids the pending decision so it leaves the Approvals
        queue. The worktree is reaped on the next tick (ticket no longer active/blocked)."""
        tid = int(f["ticket_id"])
        with db.immediate(conn):
            n = conn.execute(
                "UPDATE ticket SET status='done', claim_epoch=claim_epoch+1,"
                " blocked_by_decision_id=NULL, assigned_worker=NULL,"
                " updated_at=datetime('now') WHERE id=? AND status!='done'", (tid,)).rowcount
            if n:
                conn.execute("DELETE FROM lease WHERE ticket_id=?", (tid,))
                conn.execute("UPDATE decision SET status='rejected', consumed=1,"
                             " answered_at=datetime('now')"
                             " WHERE ticket_id=? AND status='pending'", (tid,))
                db.append_audit(conn, "human", "closed", "closed by human: no longer relevant",
                                ticket_id=tid)
        return n

    def _revive(self, conn, f):
        conn.execute("UPDATE ticket SET status='inbox', park_reason=NULL,"
                     " score=NULL WHERE id=? AND status='parked'", (f["ticket_id"],))
        db.append_audit(conn, "human", "revived", "parked ticket sent back to inbox",
                        ticket_id=int(f["ticket_id"]))

    def _pause(self, conn, f):
        """Pause an active ticket: same stop recipe as _close (fence the in-flight
        worker via claim_epoch bump, drop the lease) but park it with sub_stage and
        handler_state intact so /ui/resume re-enters the same stage."""
        tid = int(f["ticket_id"])
        with db.immediate(conn):
            n = conn.execute(
                "UPDATE ticket SET status='parked', park_reason='paused by human',"
                " claim_epoch=claim_epoch+1, assigned_worker=NULL,"
                " updated_at=datetime('now') WHERE id=? AND status='active'", (tid,)).rowcount
            if n:
                conn.execute("DELETE FROM lease WHERE ticket_id=?", (tid,))
                db.append_audit(conn, "human", "paused", "paused by human",
                                ticket_id=tid)
        return n

    def _resume(self, conn, f):
        """Resume a paused ticket in place: back to 'active' at its current sub_stage
        (unlike _revive, which restarts triage from the inbox). Guarded on sub_stage:
        a triage-parked ticket never ran, so 'resume' means nothing for it."""
        tid = int(f["ticket_id"])
        with db.immediate(conn):
            n = conn.execute(
                "UPDATE ticket SET status='active', park_reason=NULL, attempts=0,"
                " updated_at=datetime('now')"
                " WHERE id=? AND status='parked' AND sub_stage IS NOT NULL", (tid,)).rowcount
            if n:
                db.append_audit(conn, "human", "resumed",
                                "resumed by human at its paused stage", ticket_id=tid)
        return n

    def _factors(self, conn, f):
        tid = int(f["ticket_id"])
        # Clamp to the 1..5 scale the score model assumes (a raw POST could send 99).
        vals = {k: min(5, max(1, int(f[k]))) for k in ("impact", "urgency", "confidence", "effort")}
        score = scoring.compute_score(**vals)
        with db.immediate(conn):
            conn.execute("UPDATE ticket SET impact=?, urgency=?, confidence=?, effort=?, score=?,"
                         " updated_at=datetime('now') WHERE id=?",
                         (vals["impact"], vals["urgency"], vals["confidence"], vals["effort"],
                          score, tid))
            db.append_audit(conn, "human", "rescored",
                            f"score {score} = (I{vals['impact']} x U{vals['urgency']} x C{vals['confidence']}) / E{vals['effort']}",
                            ticket_id=tid)

    # -- SPA JSON (same trust zone as the HTML pages: network/Caddy protected) -----
    def _ui_post(self, conn, path):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            return self._json_send({"error": "bad json"}, 400)
        try:
            return self._ui_dispatch(conn, path, body)
        except (KeyError, ValueError, TypeError):
            # malformed body (missing key, non-numeric id) — 400, not a dropped connection
            return self._json_send({"error": "bad request"}, 400)

    def _ui_dispatch(self, conn, path, body):
        if path == "/ui/add":
            title = (body.get("title") or "").strip()
            if not title:
                return self._json_send({"error": "title required"}, 400)
            kind = taxonomy.normalize_kind(body.get("kind"))
            type_ = taxonomy.type_for(kind)
            repo, rerr = git_ops.normalize_repo_path(body.get("repo_path"))
            if rerr:
                return self._json_send({"error": rerr}, 400)
            # Draft by default (same rule as /add): pass "draft": false to start immediately.
            draft = 0 if body.get("draft") is False else 1
            with db.immediate(conn):
                cur = conn.execute(
                    "INSERT INTO ticket(title, body, type, kind, repo_path, project, draft)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (title, (body.get("body") or "").strip(), type_, kind,
                     repo if type_ == "coding" else None,
                     (body.get("project") or "").strip() or None, draft))
                tid = cur.lastrowid
                db.append_audit(conn, "human", "created",
                                f"ticket added via UI ({kind}{', draft' if draft else ''})",
                                ticket_id=tid)
            return self._json_send({"id": tid, "draft": bool(draft)})
        if path == "/ui/start":
            ok = self._start(conn, int(body.get("id", 0)))
            return self._json_send({"ok": bool(ok)} if ok else {"error": "not a draft"},
                                   200 if ok else 409)
        if path == "/ui/edit":
            # Edit a ticket's fields. A DRAFT gets a full edit (incl. kind/type — nothing
            # has acted on it). A ticket already in the pipeline can still be edited
            # (title/body/project/repo_path only — kind/type are structural: sub_stage
            # belongs to the current handler's lifecycle), EXCEPT while a worker holds a
            # live lease on it (mid-stage, acting on its content right now) or once done.
            tid = int(body["ticket_id"])
            title = (body.get("title") or "").strip()
            if not title:
                return self._json_send({"error": "title required"}, 400)
            repo, rerr = git_ops.normalize_repo_path(body.get("repo_path"))
            if rerr:
                return self._json_send({"error": rerr}, 400)
            proj = (body.get("project") or "").strip() or None
            with db.immediate(conn):
                t = conn.execute("SELECT * FROM ticket WHERE id=?", (tid,)).fetchone()
                if not t:
                    return self._json_send({"error": "not found"}, 404)
                if t["status"] == "inbox" and t["draft"]:
                    kind = taxonomy.normalize_kind(body.get("kind"))
                    type_ = taxonomy.type_for(kind)
                    conn.execute(
                        "UPDATE ticket SET title=?, body=?, kind=?, type=?, repo_path=?,"
                        " project=?, updated_at=datetime('now') WHERE id=?",
                        (title, (body.get("body") or "").strip(), kind, type_,
                         repo if type_ == "coding" else None, proj, tid))
                    db.append_audit(conn, "human", "edited", "draft edited via UI",
                                    ticket_id=tid)
                    return self._json_send({"ok": True})
                if t["status"] == "done":
                    return self._json_send({"error": "a done ticket can't be edited"}, 409)
                leased = conn.execute("SELECT 1 FROM lease WHERE ticket_id=?",
                                      (tid,)).fetchone()
                if leased:
                    return self._json_send(
                        {"error": "a worker is acting on this ticket right now — retry"
                                  " in a moment"}, 409)
                conn.execute(
                    "UPDATE ticket SET title=?, body=?, repo_path=?, project=?,"
                    " updated_at=datetime('now') WHERE id=?",
                    (title, (body.get("body") or "").strip(),
                     repo if t["type"] == "coding" else None, proj, tid))
                db.append_audit(conn, "human", "edited",
                                f"edited via UI (status {t['status']})", ticket_id=tid)
            return self._json_send({"ok": True})
        # The reply/retry/dismiss handlers read the same keys the form posts do, so the
        # JSON body doubles as the form dict — same proven write path, JSON response.
        if path == "/ui/answer":
            if not self._answer(conn, body):
                return self._json_send({"error": "decision already answered"}, 409)
            return self._json_send({"ok": True})
        if path == "/ui/comment":
            # An operator note: shown in the item's thread AND threaded into
            # handler_state so the worker sees it on its next run (the same channel
            # answered clarifications use — the author prompt already renders it).
            tid = int(body["ticket_id"])
            note = (body.get("note") or "").strip()
            if not note:
                return self._json_send({"error": "note required"}, 400)
            with db.immediate(conn):
                # Read INSIDE the write txn: the hub is threaded, and a worker op
                # committing between a pre-txn read and this write would be clobbered
                # by the stale handler_state snapshot (branch/pr state lost).
                t = conn.execute("SELECT * FROM ticket WHERE id=?", (tid,)).fetchone()
                if not t:
                    return self._json_send({"error": "not found"}, 404)
                hs = db.hstate(t)
                hs.setdefault("clarifications", []).append({"q": "(operator note)", "a": note})
                conn.execute("UPDATE ticket SET handler_state=?, updated_at=datetime('now')"
                             " WHERE id=?", (json.dumps(hs), tid))
                db.append_audit(conn, "human", "commented", note, ticket_id=tid)
            return self._json_send({"ok": True})
        if path == "/ui/set-project":
            # Re-tag any ticket's project (drafts get it via /ui/edit; this covers the
            # rest — a label change is safe mid-run, nothing reads it for routing).
            tid = int(body["ticket_id"])
            proj = (body.get("project") or "").strip() or None
            n = conn.execute("UPDATE ticket SET project=?, updated_at=datetime('now')"
                             " WHERE id=?", (proj, tid)).rowcount
            if not n:  # unknown ticket: no phantom audit row, no fake ok
                return self._json_send({"error": "not found"}, 404)
            db.append_audit(conn, "human", "project_set",
                            f"project -> {proj or '(none)'}", ticket_id=tid)
            return self._json_send({"ok": True})
        if path == "/ui/factors":
            self._factors(conn, body)
            return self._json_send({"ok": True})
        if path == "/ui/retry":
            self._retry(conn, body)
            return self._json_send({"ok": True})
        if path == "/ui/dismiss":
            self._dismiss(conn, body)
            return self._json_send({"ok": True})
        if path == "/ui/close":
            ok = self._close(conn, body)
            return self._json_send({"ok": True} if ok else {"error": "already closed"},
                                   200 if ok else 409)
        if path == "/ui/revive":
            self._revive(conn, body)
            return self._json_send({"ok": True})
        if path == "/ui/pause":
            ok = self._pause(conn, body)
            return self._json_send({"ok": True} if ok else {"error": "not active"},
                                   200 if ok else 409)
        if path == "/ui/resume":
            ok = self._resume(conn, body)
            return self._json_send({"ok": True} if ok else {"error": "not paused"},
                                   200 if ok else 409)
        if path == "/ui/worker-control":
            from . import api
            code, resp = api._control(conn, body["worker"], {"action": body.get("action"),
                                                             "ticket_id": body.get("ticket_id"),
                                                             "new_name": body.get("new_name")})
            return self._json_send(resp, code)
        if path == "/ui/worker-caps":
            from . import api
            caps = [c for c in re.split(r"[,\s]+", (body.get("capabilities") or "").strip()) if c]
            code, resp = api._control(conn, body["worker"], {"action": "set_caps", "capabilities": caps})
            return self._json_send(resp, code)
        if path == "/ui/worker-pair":
            name = (body.get("worker") or "").strip()
            if not name:
                return self._json_send({"error": "worker name required"}, 400)
            import secrets
            from . import auth
            token = secrets.token_hex(24)
            auth.set_token(conn, name, token)
            db.append_audit(conn, "human", "worker_paired", f"{name}: token issued via UI")
            return self._json_send({"worker": name, "token": token})
        if path == "/ui/run-tick":
            from .tick import run_tick
            run_tick()
            return self._json_send({"ok": True})
        if path == "/ui/pair-confirm":
            ok, err = pairing.confirm(conn, str(body.get("request_id") or ""),
                                      str(body.get("code") or ""))
            return self._json_send({"ok": True} if ok else {"error": err}, 200 if ok else 400)
        if path == "/ui/pair-ignore":
            pairing.ignore(str(body.get("request_id") or ""))
            return self._json_send({"ok": True})
        if path == "/ui/kill-switch":
            # Global stop: claim.py refuses every new claim while it's on. In-flight
            # stages finish (or are paused per-worker); this is the fleet-wide brake.
            on = bool(body.get("on"))
            db.set_setting(conn, "kill_switch", "on" if on else "off")
            db.append_audit(conn, "human", "kill_switch",
                            f"kill switch turned {'on' if on else 'off'} via UI")
            return self._json_send({"ok": True, "on": on})
        return self._json_send({"error": "not found"}, 404)

    @staticmethod
    def _stale_days(t):
        # Monday's "stuck" signal: a live ticket nobody has touched in 2+ days.
        if t["status"] not in ("active", "blocked"):
            return None
        try:
            dt = datetime.strptime(t["updated_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
        days = (datetime.now(timezone.utc) - dt).days
        return days if days >= 2 else None

    def _card(self, t, wait=None):
        kind = taxonomy.normalize_kind(t["kind"], t["type"])
        m = taxonomy.meta(kind)
        # Which machine is on it — live lease first, claim assignment as fallback
        # (same rule as _inbox_json). Only meaningful while the ticket is active.
        lw = t["lworker"] if "lworker" in t.keys() else None
        card = {"id": t["id"], "title": t["title"], "kind": kind,
                "kind_label": m["label"], "kind_color": m["color"], "type": t["type"],
                "status": t["status"], "sub_stage": t["sub_stage"], "score": t["score"],
                "breakdown": scoring.breakdown(t), "project": t["project"],
                "worker": (lw or t["assigned_worker"]) if t["status"] == "active" else None,
                "draft": bool(t["draft"]), "stale_days": self._stale_days(t)}
        if wait is not None:
            card["wait"] = wait
        return card

    def _board_json(self, conn, project=None):
        pf = " AND project=?" if project else ""
        pa = (project,) if project else ()
        q = lambda sql, *a: conn.execute(sql, a).fetchall()
        inbox = q("SELECT * FROM ticket WHERE status='inbox'" + pf
                  + " ORDER BY score IS NULL, score DESC, created_at ASC", *pa)
        active = q("SELECT * FROM ticket WHERE status='active'" + pf
                   + " ORDER BY score IS NULL, score DESC, updated_at DESC", *pa)
        blocked = q("SELECT t.*, d.kind AS dkind FROM ticket t"
                    " LEFT JOIN decision d ON d.id=t.blocked_by_decision_id"
                    " WHERE t.status='blocked'" + (" AND t.project=?" if project else "")
                    + " ORDER BY t.updated_at DESC", *pa)
        done = q("SELECT * FROM ticket WHERE status='done'" + pf
                 + " AND updated_at >= datetime('now','-7 days')"
                 " ORDER BY updated_at DESC LIMIT 15", *pa)
        done_total = conn.execute(
            "SELECT COUNT(*) c FROM ticket WHERE status='done'" + pf, pa).fetchone()["c"]
        failed = conn.execute("SELECT COUNT(*) c FROM ticket WHERE status='failed'").fetchone()["c"]
        return {
            "columns": {
                "inbox": [self._card(t) for t in inbox],
                "active": [self._card(t) for t in active],
                "blocked": [self._card(t, wait=t["dkind"]) for t in blocked],
                "done": [self._card(t) for t in done],
            },
            "counts": {"inbox": len(inbox), "active": len(active), "blocked": len(blocked),
                       "done": len(done), "done_total": done_total, "failed": failed},
            "projects": self._projects(conn),
        }

    def _done_json(self, conn, project=None):
        pf = " AND project=?" if project else ""
        pa = (project,) if project else ()
        rows = conn.execute("SELECT * FROM ticket WHERE status='done'" + pf
                            + " ORDER BY updated_at DESC LIMIT 200", pa).fetchall()
        out = []
        for t in rows:
            kind = taxonomy.normalize_kind(t["kind"], t["type"])
            m = taxonomy.meta(kind)
            out.append({"id": t["id"], "title": t["title"], "kind": kind,
                        "kind_label": m["label"], "kind_color": m["color"], "type": t["type"],
                        "project": t["project"], "updated_at": t["updated_at"]})
        return {"tickets": out}

    def _decisions_json(self, conn):
        # Every ticket that needs a human: one with a pending decision (a question or a
        # gated action), plus any failed ticket. One entry per item, newest question first.
        decs = conn.execute(
            "SELECT d.id AS decision_id, d.kind AS dkind, d.question, d.context,"
            " d.created_at AS at, t.id AS tid, t.title, t.kind, t.type, t.project"
            " FROM decision d JOIN ticket t ON t.id=d.ticket_id"
            " WHERE d.status='pending' ORDER BY d.created_at").fetchall()
        errs = conn.execute("SELECT * FROM ticket WHERE status='failed'"
                            " ORDER BY updated_at DESC").fetchall()
        out = []
        for d in decs:
            m = taxonomy.meta(taxonomy.normalize_kind(d["kind"], d["type"]))
            # decision_id + context let Approvals act inline (same /ui/answer the
            # ticket page posts) and render the pr/diff/checks meta line.
            out.append({"id": d["tid"], "title": d["title"],
                        "kind_label": m["label"], "kind_color": m["color"],
                        "project": d["project"],
                        "reason": "question" if d["dkind"] == "clarification" else d["dkind"],
                        "preview": d["question"], "at": d["at"],
                        "decision_id": d["decision_id"],
                        "context": _ctx_public(json.loads(d["context"] or "{}"))})
        for t in errs:
            m = taxonomy.meta(taxonomy.normalize_kind(t["kind"], t["type"]))
            r = conn.execute("SELECT reason FROM audit WHERE ticket_id=? AND action='failed'"
                             " ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
            out.append({"id": t["id"], "title": t["title"],
                        "kind_label": m["label"], "kind_color": m["color"],
                        "project": t["project"], "reason": "error",
                        "preview": r["reason"] if r else "failed", "at": t["updated_at"]})
        return {"tickets": out}

    # Recent ticket outcomes worth surfacing in the Inbox "today" digest, mapped to a
    # dot tone. Terminal advances (merged/finished) read green; failures red; the rest
    # (parked/closed/dismissed) muted.
    _DIGEST_DOT = {"merged": "ok", "finished": "ok", "fail": "bad", "failed": "bad",
                   "parked": "muted", "closed": "muted", "dismissed": "muted"}

    def _inbox_json(self, conn):
        """The Inbox's lower two sections. "Waiting on you" reuses decisions.json;
        this adds in-progress runs (worker · lease age · latest transcript line) and a
        today digest of recent outcomes. Ordering/queries only — no new state."""
        from .claim import _caps_ok
        # Caps of every online worker — decides whether an unleased active ticket is
        # merely queued or unclaimable (no capable worker). A fleet-less install
        # (bare `serve` + cron tick, no worker rows) has no caps to check: skip the
        # unclaimable verdict there, everything unleased is just queued.
        online_caps = [set(json.loads(w["capabilities"] or "[]")) for w in conn.execute(
            "SELECT capabilities FROM worker WHERE status='online'"
            " AND last_seen >= datetime('now', ?)",
            (f"-{config.WORKER_OFFLINE_SEC} seconds",)).fetchall()]
        have_fleet = conn.execute("SELECT COUNT(*) c FROM worker").fetchone()["c"] > 0
        running = []
        for t in conn.execute(
                "SELECT t.*, l.ticket_id AS leased, l.worker AS lworker,"
                " l.acquired_at AS since"
                " FROM ticket t LEFT JOIN lease l ON l.ticket_id=t.id"
                " WHERE t.status='active' ORDER BY t.score IS NULL, t.score DESC,"
                " t.updated_at DESC LIMIT 12").fetchall():
            ev = conn.execute("SELECT body FROM agent_event WHERE ticket_id=? AND kind='text'"
                              " ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
            m = taxonomy.meta(taxonomy.normalize_kind(t["kind"], t["type"]))
            req = json.loads(t["requires"] or "[]")
            state = ("running" if t["leased"] else
                     "queued" if not have_fleet
                     or any(_caps_ok(req, caps) for caps in online_caps)
                     else "unclaimable")
            running.append({"id": t["id"], "title": t["title"],
                            "kind_label": m["label"], "kind_color": m["color"],
                            "sub_stage": t["sub_stage"], "score": t["score"],
                            "type": t["type"], "state": state,
                            "requires": req,
                            "worker": t["lworker"] or t["assigned_worker"],
                            "since": t["since"] or t["updated_at"],
                            "last_line": ev["body"] if ev else None})
        digest = []
        for a in conn.execute(
                "SELECT a.action, a.reason, a.created_at, a.ticket_id, t.title"
                " FROM audit a JOIN ticket t ON t.id=a.ticket_id"
                " WHERE a.action IN ('merged','finished','fail','failed','parked','closed','dismissed')"
                " AND a.created_at >= datetime('now','-24 hours')"
                " ORDER BY a.id DESC LIMIT 8").fetchall():
            digest.append({"id": a["ticket_id"], "title": a["title"],
                           "dot": self._DIGEST_DOT.get(a["action"], "muted"),
                           "what": a["reason"], "at": a["created_at"]})
        drafts = conn.execute("SELECT COUNT(*) c FROM ticket WHERE status='inbox'"
                              " AND draft=1").fetchone()["c"]
        return {"running": running, "digest": digest, "drafts": drafts}

    def _tickets_json(self, conn, project=None):
        """Every ticket in one flat list for the Board (status is a client-side filter,
        not a page): all live + on-hold, plus a recent slice of done. Counts drive the
        filter chips."""
        pf = " AND t.project=?" if project else ""
        pa = (project,) if project else ()
        live = conn.execute(
            "SELECT t.*, d.kind AS dkind, l.worker AS lworker FROM ticket t"
            " LEFT JOIN decision d ON d.id=t.blocked_by_decision_id"
            " LEFT JOIN lease l ON l.ticket_id=t.id"
            " WHERE t.status IN ('inbox','active','blocked','parked','failed')" + pf
            + " ORDER BY t.score IS NULL, t.score DESC, t.updated_at DESC", pa).fetchall()
        dpf = " AND project=?" if project else ""
        done = conn.execute("SELECT * FROM ticket WHERE status='done'" + dpf
                            + " ORDER BY updated_at DESC LIMIT 60", pa).fetchall()
        tickets = [self._card(t, wait=t["dkind"]) for t in live] + [self._card(t) for t in done]
        counts = {"backlog": 0, "active": 0, "blocked": 0, "onhold": 0, "failed": 0}
        for t in live:
            counts[{"inbox": "backlog", "active": "active", "blocked": "blocked",
                    "parked": "onhold", "failed": "failed"}[t["status"]]] += 1
        counts["done"] = conn.execute(
            "SELECT COUNT(*) c FROM ticket WHERE status='done'" + dpf, pa).fetchone()["c"]
        # failed counts as open: it's unresolved work needing a human — the default
        # Board view must not hide it (it is also the top of the Inbox).
        counts["open"] = counts["backlog"] + counts["active"] + counts["blocked"] + counts["failed"]
        counts["all"] = counts["open"] + counts["onhold"] + counts["done"]
        return {"tickets": tickets, "counts": counts, "projects": self._projects(conn),
                "repos": self._repos(conn)}

    def _ticket_json(self, conn, tid):
        # A ticket's thread: its description plus a Jira-style back-and-forth built from the
        # decision table — each decision is a question from claude, its answer_note the human
        # reply. A pending decision is what the reply box acts on (answering resumes the loop).
        t = conn.execute("SELECT * FROM ticket WHERE id=?", (tid,)).fetchone()
        if not t:
            return None
        m = taxonomy.meta(taxonomy.normalize_kind(t["kind"], t["type"]))
        comments, pending = [], None
        for d in conn.execute("SELECT * FROM decision WHERE ticket_id=? ORDER BY created_at, id",
                              (tid,)).fetchall():
            ctx = _ctx_public(json.loads(d["context"] or "{}"))
            comments.append({"author": "claude", "kind": d["kind"], "body": d["question"],
                             "context": ctx, "at": d["created_at"]})
            if d["status"] in ("approved", "rejected"):
                reply = {"author": "you", "body": d["answer_note"] or "",
                         "at": d["answered_at"] or d["created_at"]}
                # An answered clarification is a reply, not a go/no-go — no verdict pill.
                if d["kind"] != "clarification":
                    reply["verdict"] = "rework" if d["rework"] else d["status"]
                comments.append(reply)
            elif d["status"] == "pending":
                pending = {"decision_id": d["id"], "kind": d["kind"], "context": ctx}
        # Operator notes live in the audit trail (append-only, timestamped).
        for a in conn.execute("SELECT reason, created_at FROM audit WHERE ticket_id=?"
                              " AND action='commented' ORDER BY id", (tid,)).fetchall():
            comments.append({"author": "you", "kind": "note",
                             "body": a["reason"], "at": a["created_at"]})
        steps = []
        if t["status"] == "failed":
            r = conn.execute("SELECT reason FROM audit WHERE ticket_id=? AND action='failed'"
                             " ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
            comments.append({"author": "system", "kind": "error",
                             "body": r["reason"] if r else "failed", "at": t["updated_at"]})
            steps = [{"action": s["action"], "reason": s["reason"]} for s in reversed(
                conn.execute("SELECT action, reason FROM audit WHERE ticket_id=?"
                             " ORDER BY id DESC LIMIT 5", (tid,)).fetchall())]
        comments.sort(key=lambda c: c["at"] or "")
        runs = [{"role": r["role"], "model": r["model"], "tokens_in": r["tokens_in"],
                 "tokens_out": r["tokens_out"], "exit_code": r["exit_code"],
                 "at": r["created_at"]}
                for r in conn.execute("SELECT * FROM agent_run WHERE ticket_id=?"
                                      " ORDER BY id", (tid,)).fetchall()]
        # Live activity: the streamed back-and-forth (text / tool / tool_result) of
        # recent agent runs, newest 50, oldest-first for chronological display.
        events = [{"role": e["role"], "kind": e["kind"], "body": e["body"],
                   "at": e["created_at"]}
                  for e in reversed(conn.execute(
                      "SELECT role, kind, body, created_at FROM agent_event"
                      " WHERE ticket_id=? ORDER BY id DESC LIMIT 50", (tid,)).fetchall())]
        lease = conn.execute("SELECT worker FROM lease WHERE ticket_id=?", (tid,)).fetchone()
        worker = ((lease["worker"] if lease else None) or t["assigned_worker"]) \
            if t["status"] == "active" else None
        return {
            "ticket": {"id": t["id"], "title": t["title"], "body": t["body"],
                       "kind": taxonomy.normalize_kind(t["kind"], t["type"]),
                       "kind_label": m["label"], "kind_color": m["color"],
                       "status": t["status"], "sub_stage": t["sub_stage"],
                       "project": t["project"], "repo_path": t["repo_path"],
                       "worker": worker, "draft": bool(t["draft"])},
            "factors": {k: t[k] for k in ("impact", "urgency", "confidence", "effort")},
            "score": t["score"], "breakdown": scoring.breakdown(t),
            "comments": comments, "pending": pending, "failed": t["status"] == "failed",
            "steps": steps, "runs": runs, "events": events,
        }

    def _fleet_json(self, conn):
        # Same queries as the server-rendered _fleet, JSON-shaped for the SPA.
        spent = conn.execute("SELECT COALESCE(SUM(tokens_in + tokens_out),0) s FROM agent_run"
                             " WHERE created_at > datetime('now', ?)",
                             (f"-{config.FLEET_SPEND_WINDOW_HOURS} hours",)).fetchone()["s"]
        cap = int(db.get_setting(conn, "fleet_budget_tokens", config.FLEET_BUDGET_TOKENS))
        workers = []
        for d in conn.execute("SELECT * FROM worker ORDER BY name").fetchall():
            age = None
            if d["last_seen"]:
                age = conn.execute("SELECT (julianday('now')-julianday(?))*86400 a",
                                   (d["last_seen"],)).fetchone()["a"]
            online = age is not None and age < config.WORKER_OFFLINE_SEC
            state = d["status"] if d["status"] != "online" else ("online" if online else "offline")
            workers.append({"name": d["name"], "state": state,
                            "capabilities": json.loads(d["capabilities"] or "[]"),
                            "seen_sec": round(age) if age is not None else None,
                            "current_ticket": d["current_ticket"], "version": d["version"]})
        # Every tag the fleet knows about, for the caps picker: the seed defaults,
        # whatever any worker already carries, and what live tickets require.
        known = set(config.DEFAULT_CAPS)
        for w in workers:
            known.update(w["capabilities"])
        for r in conn.execute("SELECT DISTINCT requires FROM ticket"
                              " WHERE status IN ('inbox','active','blocked')").fetchall():
            known.update(json.loads(r["requires"] or "[]"))
        return {"spend": {"spent": spent, "cap": cap, "halted": spent >= cap,
                          "window_hours": config.FLEET_SPEND_WINDOW_HOURS},
                "kill_switch": db.get_setting(conn, "kill_switch", "off") == "on",
                "known_caps": sorted(known),
                "version": __version__,  # fills the status strip's mono slot with a real datum
                "workers": workers}

    def _parked_json(self, conn):
        out = []
        for t in conn.execute("SELECT * FROM ticket WHERE status='parked'"
                              " ORDER BY created_at DESC").fetchall():
            m = taxonomy.meta(taxonomy.normalize_kind(t["kind"], t["type"]))
            out.append({"id": t["id"], "title": t["title"], "kind_label": m["label"],
                        "kind_color": m["color"], "project": t["project"],
                        "park_reason": t["park_reason"], "created_at": t["created_at"]})
        return {"tickets": out}

    def _log_json(self, conn):
        rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200").fetchall()
        return {"events": [
            {"id": a["id"], "at": a["created_at"], "actor": a["actor"], "action": a["action"],
             "ticket_id": a["ticket_id"], "reason": a["reason"],
             "detail": a["detail"] if a["detail"] not in ("{}", "") else None}
            for a in rows]}

    def _search_json(self, conn, q):
        # Quick search across ALL statuses (board only shows recent/done-7d). Plain
        # LIKE over title/body/project + exact id ("#12" or "12"), newest-touched first.
        q = (q or "").strip()
        if not q:
            return {"tickets": []}
        # Escape LIKE metacharacters so "%"/"_" in the query match literally.
        esc_q = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        like, ident = f"%{esc_q}%", q.lstrip("#")
        rows = conn.execute(
            "SELECT * FROM ticket WHERE title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\'"
            " OR COALESCE(project,'') LIKE ? ESCAPE '\\' OR CAST(id AS TEXT)=?"
            " ORDER BY updated_at DESC LIMIT 30", (like, like, like, ident)).fetchall()
        out = []
        for t in rows:
            m = taxonomy.meta(taxonomy.normalize_kind(t["kind"], t["type"]))
            status = "draft" if t["status"] == "inbox" and t["draft"] else t["status"]
            out.append({"id": t["id"], "title": t["title"], "kind_label": m["label"],
                        "kind_color": m["color"], "project": t["project"],
                        "status": status, "updated_at": t["updated_at"]})
        return {"tickets": out}

    def _insights_json(self, conn):
        # The operator dashboard: throughput, spend, failure rate — the few numbers
        # that answer "is the loop healthy and what is it costing me".
        q = lambda sql, *a: conn.execute(sql, a).fetchall()
        tok = {r["d"]: r["t"] for r in q(
            "SELECT date(created_at) d, SUM(tokens_in + tokens_out) t FROM agent_run"
            " WHERE created_at >= datetime('now','-14 days') GROUP BY d")}
        dn = {r["d"]: r["c"] for r in q(
            "SELECT date(updated_at) d, COUNT(*) c FROM ticket WHERE status='done'"
            " AND updated_at >= datetime('now','-14 days') GROUP BY d")}
        today = datetime.now(timezone.utc).date()
        days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
        one = lambda sql, *a: conn.execute(sql, a).fetchone()[0] or 0
        done_7d = one("SELECT COUNT(*) FROM ticket WHERE status='done'"
                      " AND updated_at >= datetime('now','-7 days')")
        failed_7d = one("SELECT COUNT(DISTINCT ticket_id) FROM audit WHERE action='failed'"
                        " AND created_at >= datetime('now','-7 days')")
        return {
            "days": [{"d": d, "tokens": tok.get(d, 0) or 0, "done": dn.get(d, 0)} for d in days],
            "totals": {
                "tokens_7d": one("SELECT SUM(tokens_in + tokens_out) FROM agent_run"
                                 " WHERE created_at >= datetime('now','-7 days')"),
                "done_7d": done_7d, "failed_7d": failed_7d,
                "active": one("SELECT COUNT(*) FROM ticket WHERE status='active'"),
                "blocked": one("SELECT COUNT(*) FROM ticket WHERE status='blocked'"),
                "drafts": one("SELECT COUNT(*) FROM ticket WHERE status='inbox' AND draft=1"),
            },
            "by_role": [{"role": r["role"], "tokens": r["t"]} for r in q(
                "SELECT role, SUM(tokens_in + tokens_out) t FROM agent_run"
                " WHERE created_at >= datetime('now','-7 days')"
                " GROUP BY role ORDER BY t DESC")],
            "by_project": [{"project": r["p"], "total": r["c"], "done": r["dn"]} for r in q(
                "SELECT COALESCE(project,'(none)') p, COUNT(*) c,"
                " SUM(status='done') dn FROM ticket"
                " WHERE updated_at >= datetime('now','-30 days')"
                " GROUP BY p ORDER BY c DESC LIMIT 6")],
        }

    def _requests_json(self, conn):
        rows = conn.execute("SELECT * FROM request_log ORDER BY id DESC LIMIT 200").fetchall()
        return {"cap": db.REQUEST_LOG_CAP,
                "requests": [{"id": r["id"], "at": r["created_at"], "worker": r["worker"],
                              "method": r["method"], "path": r["path"], "status": r["status"]}
                             for r in rows]}

    def _projects(self, conn):
        return [r["project"] for r in conn.execute(
            "SELECT DISTINCT project FROM ticket WHERE project IS NOT NULL AND project!=''"
            " ORDER BY project")]

    def _repos(self, conn):
        # Stored repo_path is already canonical (normalize_repo_path at intake), so
        # suggesting past values re-converges every spelling onto one clone per worker.
        # Recency order, not alpha — the create form wants "what I used lately".
        return [r["repo_path"] for r in conn.execute(
            "SELECT repo_path FROM ticket WHERE repo_path IS NOT NULL AND repo_path!=''"
            " GROUP BY repo_path ORDER BY MAX(id) DESC LIMIT 20")]

def serve(port=8765):
    db.init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"outerloop UI on http://127.0.0.1:{port}")
    srv.serve_forever()
