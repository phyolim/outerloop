"""A deliberately tiny stdlib http.server UI: add a ticket, browse the board, and
answer the decision queue. Read-mostly with small single-row writes, so it shares
the SQLite file with the cron worker via WAL without contention."""

import html
import json
import mimetypes
import re
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config, db, scoring, taxonomy

STYLE = """
<style>
 body{font:14px/1.5 -apple-system,system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#111}
 nav a{margin-right:1rem;text-decoration:none;color:#06c} h1{font-size:1.3rem}
 table{border-collapse:collapse;width:100%;margin:1rem 0} td,th{border-bottom:1px solid #eee;padding:.4rem;text-align:left;vertical-align:top}
 .badge{display:inline-block;padding:.1rem .4rem;border-radius:3px;background:#eef;font-size:.8rem}
 .s-blocked{color:#b40} .s-done{color:#7a7} .s-failed{color:#999;text-decoration:line-through} .s-active{color:#06c}
 .card{border:1px solid #ddd;border-radius:6px;padding:1rem;margin:1rem 0} .mut{color:#888;font-size:.85rem}
 .card.err{border-color:#f1c9c9;background:#fdf3f3} .errmsg{color:#b40;font-weight:600;margin:.4rem 0}
 .card.q{border-color:#cfe0f6;background:#f5f9ff} .qtext{font-weight:600;margin:.4rem 0}
 form.inline{display:inline} input,select,textarea,button{font:inherit;padding:.3rem} textarea{width:100%}
 pre{background:#f6f6f6;padding:.6rem;border-radius:4px;overflow:auto;font-size:.8rem}
</style>
"""


# Fleet-only styling, injected into the /fleet body (keeps the shared minimal STYLE
# untouched for other pages). Dependency-free: system UI font + ui-monospace for device
# identity/metrics — no web fonts, since the hub serves this on a LAN and may be offline.
FLEET_CSS = """
<style>
 .spend{display:flex;align-items:center;gap:.6rem;font-size:.82rem;color:#555;border:1px solid #ececec;
   border-radius:8px;padding:.5rem .8rem;margin:.4rem 0 1.3rem;background:#fafafa}
 .spend .bar{height:6px;border-radius:3px;background:#e9e9e9;overflow:hidden;width:180px}
 .spend .bar>i{display:block;height:100%;background:#1a7f37}
 .spend.halt{color:#b40;border-color:#f1c9c9;background:#fdf3f3}
 .spend.halt .bar>i{background:#b40}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1rem;margin:1rem 0}
 .dev{border:1px solid #e6e6e6;border-radius:12px;padding:1rem;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.04)}
 .dev:hover{box-shadow:0 4px 14px rgba(0,0,0,.07)}
 .dev.off{background:#fbfbfb;border-style:dashed}
 .dh{display:flex;align-items:center;justify-content:space-between;gap:.5rem;margin-bottom:.6rem}
 .dn{font:600 15px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:#111}
 .pill{display:inline-flex;align-items:center;gap:.35rem;font-size:.7rem;font-weight:700;
   text-transform:uppercase;letter-spacing:.05em;padding:.2rem .55rem;border-radius:999px}
 .pill:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
 .st-online{color:#1a7f37;background:#e7f5ec} .st-offline{color:#8a8a8a;background:#f0f0f0}
 .st-paused{color:#9a6700;background:#fbf1d6} .st-draining{color:#0a56c2;background:#e6effc}
 .meta{font-size:.79rem;color:#777;display:flex;gap:1.1rem;flex-wrap:wrap;margin-bottom:.6rem}
 .meta b{color:#333;font-family:ui-monospace,Menlo,monospace;font-weight:600}
 .caps{display:flex;flex-wrap:wrap;gap:.3rem;margin-bottom:.55rem}
 .chip{font:.72rem ui-monospace,Menlo,monospace;background:#eef1f6;color:#33415c;border-radius:5px;padding:.13rem .42rem}
 .chip.none{color:#9a9a9a;background:transparent;font-style:italic;padding-left:0}
 .cedit{display:flex;gap:.35rem;margin-bottom:.7rem}
 .cedit input{flex:1;min-width:0;border:1px solid #dcdcdc;border-radius:6px;padding:.3rem .45rem;font-size:.78rem}
 .cedit button,.ctl button{border:1px solid #d5d5d5;background:#fff;border-radius:7px;padding:.3rem .6rem;
   font-size:.78rem;cursor:pointer;transition:background .12s,border-color .12s}
 .cedit button:hover,.ctl button:hover{background:#f2f2f2;border-color:#bbb}
 .ctl{display:flex;gap:.4rem}
 .ctl .go{border-color:#1a7f37;color:#1a7f37} .ctl .go:hover{background:#e7f5ec}
 .ctl .warn{border-color:#c69a2e;color:#8a6d16} .ctl .warn:hover{background:#fbf3dd}
 .pair{border:1px dashed #cfcfcf;border-radius:12px;padding:1rem;background:#fafafa;margin-top:1.3rem}
 .pair input{border:1px solid #dcdcdc;border-radius:6px;padding:.35rem .5rem;font-size:.85rem}
 .pair button{border:1px solid #0a56c2;color:#0a56c2;background:#fff;border-radius:7px;padding:.35rem .8rem;cursor:pointer}
 .pair button:hover{background:#e6effc} .tip{color:#888;font-size:.79rem;margin-top:.5rem}
</style>
"""


# Board-only styling for the Jira-like inbox (columns of ticket cards). Injected into
# the / body; dependency-free (system UI font + ui-monospace), LAN-offline friendly.
BOARD_CSS = """
<style>
 .board{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.7rem;margin:1.2rem 0;
   align-items:start}
 .col{background:#fafafa;border:1px solid #ececec;border-radius:10px;padding:.55rem}
 .col h3{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:#666;
   margin:.15rem .1rem .55rem;display:flex;justify-content:space-between;font-weight:700}
 .col h3 .n{color:#aaa}
 .tk{background:#fff;border:1px solid #e6e6e6;border-radius:8px;padding:.5rem .6rem;
   margin-bottom:.5rem;box-shadow:0 1px 2px rgba(0,0,0,.04)}
 .tk a{color:#111;text-decoration:none;font-weight:600;font-size:.86rem;display:block}
 .tk a:hover{color:#06c}
 .tk .row{display:flex;justify-content:space-between;align-items:center;gap:.4rem;margin-top:.45rem}
 .tk .stage{font:.68rem ui-monospace,Menlo,monospace;background:#eef1f6;color:#33415c;
   border-radius:5px;padding:.1rem .42rem}
 .tk .stage.wait{background:#fbf1d6;color:#8a6d16}
 .tk .pri{color:#aaa;font-size:.72rem;margin-top:.3rem}
 .tk .proj{display:inline-block;font:.68rem ui-monospace,Menlo,monospace;color:#5b4bb3;
   background:#efecfb;border-radius:5px;padding:.05rem .4rem;margin-top:.35rem}
 .pfilter{margin:.4rem 0 -.4rem}
 .allts{display:block;font-size:.72rem;color:#888;text-decoration:none;margin-top:.2rem}
 .allts:hover{color:#06c}
 @media(max-width:640px){.board{grid-template-columns:1fr 1fr}}
</style>
"""


def page(title, body):
    return ("<!doctype html><meta charset=utf-8><title>" + html.escape(title) + "</title>"
            + STYLE + "<nav><a href=/>Inbox</a><a href=/decisions>Decisions</a>"
            "<a href=/fleet>Fleet</a><a href=/parked>Parked</a><a href=/log>Log</a>"
            "<form class=inline method=post action=/run-tick>"
            "<button>Run tick now</button></form></nav>" + body)


def esc(x):
    return html.escape(str(x if x is not None else ""))


def _pr_link(url):
    return f"<a href=\"{esc(url)}\" target=_blank>{esc(url)}</a>" if url else ""


def _ctx_public(ctx):
    """Whitelist the decision-context fields the SPA renders (PR, diff, checks,
    findings) — never dumps internal drafted payloads."""
    keys = ("pr_url", "diff_stat", "checks", "checks_green", "findings")
    return {k: ctx[k] for k in keys if k in ctx}


def _decision_summary(ctx):
    """Render the KNOWN decision-context fields as a readable block — PR link, diff
    stat, checks, reviewer findings. A human summary of the work, not a JSON dump;
    unknown keys are ignored."""
    parts = []
    if ctx.get("pr_url"):
        parts.append(f"<p><b>PR:</b> {_pr_link(ctx['pr_url'])}</p>")
    if ctx.get("diff_stat"):
        parts.append(f"<p class=mut>{esc(ctx['diff_stat'])}</p>")
    if "checks_green" in ctx:
        cls = "s-done" if ctx.get("checks_green") else "s-blocked"
        parts.append(f"<p><b>checks:</b> <span class={cls}>{esc(ctx.get('checks', '?'))}</span></p>")
    if ctx.get("findings"):
        items = "".join(f"<li>{esc(x)}</li>" for x in ctx["findings"])
        parts.append(f"<p><b>reviewer findings:</b></p><ul>{items}</ul>")
    return "".join(parts)


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

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def _json_send(self, obj, code=200):
        self._send(json.dumps(obj), code=code, ctype="application/json")

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

    # -- routing --------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        # The React SPA (when built) owns / and its own static assets. Absent ui/dist
        # (no-build / offline dev), everything falls through to the server-rendered
        # pages below, so the hub always has a working UI with zero toolchain.
        if config.UI_DIST.is_dir():
            if u.path == "/":
                return self._send((config.UI_DIST / "index.html").read_bytes())
            if u.path != "/" and not u.path.startswith("/ui/") and self._serve_static(u.path):
                return
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
            elif u.path == "/ui/insights.json":
                self._json_send(self._insights_json(conn))
            elif u.path == "/ui/ticket.json":
                tid = parse_qs(u.query).get("id", [None])[0]
                data = self._ticket_json(conn, int(tid)) if tid and tid.isdigit() else None
                self._json_send(data or {"error": "not found"}, 200 if data else 404)
            elif u.path == "/":
                project = parse_qs(u.query).get("project", [None])[0] or None
                self._send(page("Inbox", self._inbox(conn, project)))
            elif u.path == "/done":
                project = parse_qs(u.query).get("project", [None])[0] or None
                self._send(page("Done", self._done_page(conn, project)))
            elif u.path == "/decisions":
                self._send(page("Decisions", self._decisions(conn)))
            elif u.path == "/parked":
                self._send(page("Parked", self._parked(conn)))
            elif u.path == "/fleet":
                self._send(page("Fleet", self._fleet(conn)))
            elif u.path == "/log":
                self._send(page("Log", self._log(conn)))
            elif u.path == "/log/raw":
                self._send(page("Raw requests", self._raw_log(conn)))
            elif u.path.startswith("/ticket/"):
                self._send(page("Ticket", self._ticket(conn, int(u.path.split("/")[-1]))))
            else:
                self._send("not found", 404)
        finally:
            conn.close()

    def do_POST(self):
        u = urlparse(self.path)
        conn = db.connect()
        try:
            if u.path.startswith("/ui/"):
                return self._ui_post(conn, u.path)
            f = self._form()
            if u.path == "/add":
                kind = taxonomy.normalize_kind(f.get("kind"))
                type_ = taxonomy.type_for(kind)
                repo = (f.get("repo_path") or "").strip() or None
                # UI adds land as DRAFTS by default: a dropped idea must not get picked up
                # (triaged/scored/worked) until the human explicitly starts it.
                draft = 0 if f.get("start") else 1
                conn.execute("INSERT INTO ticket(title, body, type, kind, repo_path, project, draft)"
                             " VALUES(?,?,?,?,?,?,?)",
                             (f.get("title", "").strip(), f.get("body", "").strip(),
                              type_, kind, repo if type_ == "coding" else None,
                              f.get("project", "").strip() or None, draft))
                db.append_audit(conn, "human", "created",
                                f"ticket added via UI ({kind}{', draft' if draft else ''})")
                self._redirect("/")
            elif u.path == "/start":
                self._start(conn, int(f["ticket_id"]))
                self._redirect("/")
            elif u.path == "/set-project":
                tid = int(f["ticket_id"])
                proj = f.get("project", "").strip() or None
                conn.execute("UPDATE ticket SET project=?, updated_at=datetime('now') WHERE id=?",
                             (proj, tid))
                db.append_audit(conn, "human", "project_set",
                                f"project -> {proj or '(none)'}", ticket_id=tid)
                self._redirect(f"/ticket/{tid}")
            elif u.path == "/answer":
                self._answer(conn, f)
                self._redirect("/decisions")
            elif u.path == "/factors":
                self._factors(conn, f)
                self._redirect(f"/ticket/{f['ticket_id']}")
            elif u.path == "/retry":
                self._retry(conn, f)
                self._redirect("/decisions")
            elif u.path == "/dismiss":
                self._dismiss(conn, f)
                self._redirect("/decisions")
            elif u.path == "/close":
                self._close(conn, f)
                self._redirect(f"/ticket/{f['ticket_id']}")
            elif u.path == "/revive":
                self._revive(conn, f)
                self._redirect("/parked")
            elif u.path == "/run-tick":
                from .tick import run_tick
                run_tick()
                self._redirect("/")
            elif u.path == "/device-control":
                from . import api
                api._control(conn, f["device"], {"action": f.get("action"),
                                                 "ticket_id": f.get("ticket_id")})
                self._redirect("/fleet")
            elif u.path == "/device-caps":
                from . import api
                caps = [c for c in re.split(r"[,\s]+", f.get("capabilities", "").strip()) if c]
                api._control(conn, f["device"], {"action": "set_caps", "capabilities": caps})
                self._redirect("/fleet")
            elif u.path == "/device-pair":
                self._pair(conn, f.get("device", "").strip())
            else:
                self._send("not found", 404)
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
                " blocked_by_decision_id=NULL, assigned_device=NULL,"
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

    def _pair(self, conn, name):
        # Issue a fresh token for a device (creating it if new). The hub keeps only the
        # hash, so this is the one moment the raw token exists — show it once, then it's
        # copied into that machine's worker (Settings -> Device + Token).
        if not name:
            self._redirect("/fleet")
            return
        import secrets
        from . import auth
        token = secrets.token_hex(24)
        auth.set_token(conn, name, token)
        db.append_audit(conn, "human", "device_paired", f"{name}: token issued via UI")
        body = (
            f"<h1>Device &ldquo;{esc(name)}&rdquo; paired</h1>"
            f"<p>On <b>{esc(name)}</b>, open the menu-bar <b>Settings…</b>, set "
            f"<b>Device</b> to <code>{esc(name)}</code>, paste this <b>Token</b>, and Save:</p>"
            f"<pre>{esc(token)}</pre>"
            f"<p class=mut>Shown once — the hub stores only a hash and can't display it "
            f"again. Re-pair to issue a new token (the old one stops working).</p>"
            f"<p><a href=/fleet>&larr; Back to Fleet</a></p>")
        self._send(page("Device paired", body))

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
            repo = (body.get("repo_path") or "").strip() or None
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
            # Edit a DRAFT's fields. Guarded to drafts only — once an item is in the
            # pipeline a worker may be acting on its content mid-run.
            tid = int(body["ticket_id"])
            title = (body.get("title") or "").strip()
            if not title:
                return self._json_send({"error": "title required"}, 400)
            kind = taxonomy.normalize_kind(body.get("kind"))
            type_ = taxonomy.type_for(kind)
            repo = (body.get("repo_path") or "").strip() or None
            with db.immediate(conn):
                n = conn.execute(
                    "UPDATE ticket SET title=?, body=?, kind=?, type=?, repo_path=?,"
                    " project=?, updated_at=datetime('now')"
                    " WHERE id=? AND status='inbox' AND draft=1",
                    (title, (body.get("body") or "").strip(), kind, type_,
                     repo if type_ == "coding" else None,
                     (body.get("project") or "").strip() or None, tid)).rowcount
                if n:
                    db.append_audit(conn, "human", "edited", "draft edited via UI",
                                    ticket_id=tid)
            return self._json_send({"ok": True} if n else {"error": "only drafts can be edited"},
                                   200 if n else 409)
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
        if path == "/ui/device-control":
            from . import api
            code, resp = api._control(conn, body["device"], {"action": body.get("action"),
                                                             "ticket_id": body.get("ticket_id")})
            return self._json_send(resp, code)
        if path == "/ui/device-caps":
            from . import api
            caps = [c for c in re.split(r"[,\s]+", (body.get("capabilities") or "").strip()) if c]
            code, resp = api._control(conn, body["device"], {"action": "set_caps", "capabilities": caps})
            return self._json_send(resp, code)
        if path == "/ui/device-pair":
            name = (body.get("device") or "").strip()
            if not name:
                return self._json_send({"error": "device name required"}, 400)
            import secrets
            from . import auth
            token = secrets.token_hex(24)
            auth.set_token(conn, name, token)
            db.append_audit(conn, "human", "device_paired", f"{name}: token issued via UI")
            return self._json_send({"device": name, "token": token})
        if path == "/ui/run-tick":
            from .tick import run_tick
            run_tick()
            return self._json_send({"ok": True})
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
        card = {"id": t["id"], "title": t["title"], "kind": kind,
                "kind_label": m["label"], "kind_color": m["color"], "type": t["type"],
                "status": t["status"], "sub_stage": t["sub_stage"], "score": t["score"],
                "breakdown": scoring.breakdown(t), "project": t["project"],
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
            "SELECT d.id AS decision_id, d.kind AS dkind, d.question, d.created_at AS at,"
            " t.id AS tid, t.title, t.kind, t.type, t.project"
            " FROM decision d JOIN ticket t ON t.id=d.ticket_id"
            " WHERE d.status='pending' ORDER BY d.created_at").fetchall()
        errs = conn.execute("SELECT * FROM ticket WHERE status='failed'"
                            " ORDER BY updated_at DESC").fetchall()
        out = []
        for d in decs:
            m = taxonomy.meta(taxonomy.normalize_kind(d["kind"], d["type"]))
            out.append({"id": d["tid"], "title": d["title"],
                        "kind_label": m["label"], "kind_color": m["color"],
                        "project": d["project"],
                        "reason": "question" if d["dkind"] == "clarification" else d["dkind"],
                        "preview": d["question"], "at": d["at"]})
        for t in errs:
            m = taxonomy.meta(taxonomy.normalize_kind(t["kind"], t["type"]))
            r = conn.execute("SELECT reason FROM audit WHERE ticket_id=? AND action='failed'"
                             " ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
            out.append({"id": t["id"], "title": t["title"],
                        "kind_label": m["label"], "kind_color": m["color"],
                        "project": t["project"], "reason": "error",
                        "preview": r["reason"] if r else "failed", "at": t["updated_at"]})
        return {"tickets": out}

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
        return {
            "ticket": {"id": t["id"], "title": t["title"], "body": t["body"],
                       "kind": taxonomy.normalize_kind(t["kind"], t["type"]),
                       "kind_label": m["label"], "kind_color": m["color"],
                       "status": t["status"], "sub_stage": t["sub_stage"],
                       "project": t["project"], "repo_path": t["repo_path"],
                       "draft": bool(t["draft"])},
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
        devices = []
        for d in conn.execute("SELECT * FROM device ORDER BY name").fetchall():
            age = None
            if d["last_seen"]:
                age = conn.execute("SELECT (julianday('now')-julianday(?))*86400 a",
                                   (d["last_seen"],)).fetchone()["a"]
            online = age is not None and age < config.DEVICE_OFFLINE_SEC
            state = d["status"] if d["status"] != "online" else ("online" if online else "offline")
            devices.append({"name": d["name"], "state": state,
                            "capabilities": json.loads(d["capabilities"] or "[]"),
                            "seen_sec": round(age) if age is not None else None,
                            "current_ticket": d["current_ticket"], "version": d["version"]})
        return {"spend": {"spent": spent, "cap": cap, "halted": spent >= cap,
                          "window_hours": config.FLEET_SPEND_WINDOW_HOURS},
                "devices": devices}

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
                "requests": [{"id": r["id"], "at": r["created_at"], "device": r["device"],
                              "method": r["method"], "path": r["path"], "status": r["status"]}
                             for r in rows]}

    def _done_page(self, conn, project=None):
        # Fallback (no-build) history view; the SPA has its own. Same 200-row cap.
        rows = self._done_json(conn, project)["tickets"]
        trs = "".join(
            f"<tr><td>#{t['id']}</td>"
            f"<td><a href=/ticket/{t['id']}>{esc(t['title'])}</a></td>"
            f"<td><span class=badge style='background:{t['kind_color']}22;color:{t['kind_color']}'>"
            f"{esc(t['kind_label'])}</span></td>"
            f"<td>{esc(t['project'] or '')}</td><td class=mut>{esc(t['updated_at'])}</td></tr>"
            for t in rows)
        return ("<h1>Done history</h1><p><a href=/>&larr; Board</a></p>"
                "<table><tr><th>ID<th>Title<th>Type<th>Project<th>Finished</tr>"
                + (trs or "<tr><td colspan=5 class=mut>none yet</td></tr>") + "</table>")

    # -- views ----------------------------------------------------------------
    def _tk_card(self, t, wait=None):
        # One ticket card on the board. `wait` is the pending decision kind for a
        # blocked ticket, so a card reads "waiting: clarification" / "waiting: merge".
        is_draft = t["status"] == "inbox" and t["draft"]
        stage = (f"<span class='stage wait'>waiting: {esc(wait)}</span>" if wait
                 else "<span class='stage wait'>draft</span>" if is_draft
                 else f"<span class=stage>{esc(t['sub_stage'] or 'new')}</span>")
        pri = scoring.breakdown(t)
        proj = f"<div class=proj>{esc(t['project'])}</div>" if t["project"] else ""
        m = taxonomy.meta(taxonomy.normalize_kind(t["kind"], t["type"]))
        badge = (f"<span class=badge style='background:{m['color']}22;color:{m['color']}'>"
                 f"{esc(m['label'])}</span>")
        start = ("" if not is_draft else
                 f"<form method=post action=/start style='display:inline'>"
                 f"<input type=hidden name=ticket_id value={t['id']}>"
                 f"<button>&#9654; Start</button></form>")
        return (f"<div class=tk><a href=/ticket/{t['id']}>#{t['id']} {esc(t['title'])}</a>"
                f"<div class=row>{stage}{badge}{start}</div>"
                + (f"<div class=pri>{esc(pri)}</div>" if pri and not is_draft else "") + proj + "</div>")

    def _projects(self, conn):
        return [r["project"] for r in conn.execute(
            "SELECT DISTINCT project FROM ticket WHERE project IS NOT NULL AND project!=''"
            " ORDER BY project")]

    def _datalist(self, projects):
        return ("<datalist id=projects>"
                + "".join(f"<option value=\"{esc(p)}\">" for p in projects) + "</datalist>")

    def _inbox(self, conn, project=None):
        projects = self._projects(conn)
        pf = lambda col: f" AND {col}=?" if project else ""
        pa = (project,) if project else ()
        q = lambda sql, *a: conn.execute(sql, a).fetchall()
        inbox = q("SELECT * FROM ticket WHERE status='inbox'" + pf("project")
                  + " ORDER BY score IS NULL, score DESC, created_at ASC", *pa)
        active = q("SELECT * FROM ticket WHERE status='active'" + pf("project")
                   + " ORDER BY score IS NULL, score DESC, updated_at DESC", *pa)
        blocked = q("SELECT t.*, d.kind AS dkind FROM ticket t"
                    " LEFT JOIN decision d ON d.id=t.blocked_by_decision_id"
                    " WHERE t.status='blocked'" + pf("t.project")
                    + " ORDER BY t.updated_at DESC", *pa)
        done = q("SELECT * FROM ticket WHERE status='done'" + pf("project")
                 + " AND updated_at >= datetime('now','-7 days')"
                 " ORDER BY updated_at DESC LIMIT 15", *pa)
        done_total = conn.execute("SELECT COUNT(*) c FROM ticket WHERE status='done'"
                                  + pf("project"), pa).fetchone()["c"]
        n_err = conn.execute("SELECT COUNT(*) c FROM ticket WHERE status='failed'").fetchone()["c"]

        out = [BOARD_CSS, self._add_form(projects), self._project_filter(projects, project)]
        if n_err:
            out.append(f"<p><a href=/decisions><b>&#9888; {n_err} errored ticket(s) need you</b></a></p>")

        columns = [("Inbox", inbox, False), ("In progress", active, False),
                   ("Needs you", blocked, True), ("Done", done, False)]
        cells = []
        for title, rows, is_wait in columns:
            cards = "".join(self._tk_card(t, wait=(t["dkind"] if is_wait else None)) for t in rows)
            footer = ""
            if title == "Done" and done_total > len(rows):
                footer = f"<a class=allts href=/done>all {done_total} done &rarr;</a>"
            cells.append(f"<div class=col><h3>{title}<span class=n>{len(rows)}</span></h3>"
                         f"{cards or '<p class=mut>—</p>'}{footer}</div>")
        out.append(f"<div class=board>{''.join(cells)}</div>")
        return "".join(out)

    def _add_form(self, projects):
        opts = "".join(f"<option value={k}{' selected' if k == taxonomy.DEFAULT_KIND else ''}>"
                       f"{taxonomy.KIND_META[k]['label']}</option>" for k in taxonomy.KINDS)
        return ("<div class=card><form method=post action=/add>"
                "<input name=title placeholder='What needs doing?' size=50 required> "
                f"<select name=kind>{opts}</select> "
                "<button>Add draft</button> "
                "<label class=mut><input type=checkbox name=start value=1> start now</label>"
                "<details style='margin-top:.5rem'><summary class=mut>details</summary>"
                "<textarea name=body rows=2 placeholder='Idea / requirement / details'></textarea>"
                "<input name=project list=projects placeholder='project (optional)' size=18> "
                "<input name=repo_path placeholder='repo path (coding kinds only)' size=24>"
                "</details></form>" + self._datalist(projects) + "</div>")

    def _project_filter(self, projects, current):
        if not projects:
            return ""
        opts = ("<option value=''>All projects</option>"
                + "".join(f"<option {'selected' if p == current else ''}>{esc(p)}</option>"
                          for p in projects))
        return (f"<form class=pfilter method=get>"
                f"<select name=project onchange='this.form.submit()'>{opts}</select></form>")

    def _error_card(self, conn, t):
        # Something broke. Show WHY + the last few working steps + the PR (if any) —
        # never approve/reject. The only actions are Retry (re-run the stage) or Dismiss.
        hs = db.hstate(t)
        r = conn.execute("SELECT reason FROM audit WHERE ticket_id=? AND action='failed'"
                         " ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
        reason = r["reason"] if r else "failed"
        steps = conn.execute("SELECT action, reason FROM audit WHERE ticket_id=?"
                             " ORDER BY id DESC LIMIT 5", (t["id"],)).fetchall()
        steps_html = "".join(f"<li class=mut>{esc(s['action'])}: {esc(s['reason'])}</li>"
                             for s in reversed(steps))
        pr = f"<p><b>PR:</b> {_pr_link(hs['pr_url'])}</p>" if hs.get("pr_url") else ""
        return (
            f"<div class='card err'><b>&#9888; {esc(t['type'])} #{t['id']}</b> "
            f"<a href=/ticket/{t['id']}>{esc(t['title'])}</a>"
            f"<p class=errmsg>{esc(reason)}</p>{pr}"
            f"<details><summary class=mut>last steps</summary><ul>{steps_html}</ul></details>"
            f"<form class=inline method=post action=/retry>"
            f"<input type=hidden name=ticket_id value={t['id']}><button>Retry</button></form> "
            f"<form class=inline method=post action=/dismiss>"
            f"<input type=hidden name=ticket_id value={t['id']}><button>Dismiss</button></form></div>")

    def _decisions(self, conn):
        errs = conn.execute("SELECT * FROM ticket WHERE status='failed'"
                            " ORDER BY updated_at DESC").fetchall()
        decs = conn.execute("SELECT d.*, t.title FROM decision d JOIN ticket t ON t.id=d.ticket_id"
                            " WHERE d.status='pending' ORDER BY d.created_at").fetchall()
        out = []
        if errs:
            out.append("<h2>Errors</h2>")
            out += [self._error_card(conn, t) for t in errs]
        out.append("<h2>Decisions</h2>")
        if not decs:
            out.append("<p>No decisions pending. The loop is clear.</p>")
        for d in decs:
            ctx = json.loads(d["context"] or "{}")
            if d["kind"] == "clarification":
                # A question from the worker — the worker is waiting on YOUR answer, not a
                # go/no-go. Free-text answer box; submitting resumes the work with it.
                out.append(
                    f"<div class='card q'><b>&#63; question</b> &mdash; "
                    f"<a href=/ticket/{d['ticket_id']}>{esc(d['title'])}</a>"
                    f"<p class=qtext>{esc(d['question'])}</p>"
                    f"<form method=post action=/answer>"
                    f"<input type=hidden name=decision_id value={d['id']}>"
                    f"<textarea name=note rows=2 placeholder='your answer' required></textarea>"
                    f"<button name=action value=approve>Send answer</button></form></div>")
                continue
            summary = _decision_summary(ctx)
            out.append(
                f"<div class=card><b>{esc(d['kind'])}</b> &mdash; "
                f"<a href=/ticket/{d['ticket_id']}>{esc(d['title'])}</a>"
                f"<p>{esc(d['question'])}</p>{summary}"
                f"<form method=post action=/answer>"
                f"<input type=hidden name=decision_id value={d['id']}>"
                f"<input name=note placeholder='comment (optional)' size=40> "
                f"<button name=action value=approve>Approve</button> "
                f"<button name=action value=rework>Request changes</button> "
                f"<button name=action value=reject>Reject</button></form></div>")
        return "".join(out)

    def _parked(self, conn):
        rows = conn.execute("SELECT * FROM ticket WHERE status='parked' ORDER BY created_at DESC").fetchall()
        if not rows:
            return "<p>Parking lot is empty.</p>"
        out = ["<table><tr><th>#</th><th>title</th><th>why parked</th><th></th></tr>"]
        for t in rows:
            out.append(
                f"<tr><td>{t['id']}</td><td>{esc(t['title'])}</td><td class=mut>{esc(t['park_reason'])}</td>"
                f"<td><form method=post action=/revive>"
                f"<input type=hidden name=ticket_id value={t['id']}>"
                f"<button>Revive</button></form></td></tr>")
        out.append("</table>")
        return "".join(out)

    def _devbtn(self, name, action, label, cls=""):
        return (f"<form class=inline method=post action=/device-control>"
                f"<input type=hidden name=device value=\"{esc(name)}\">"
                f"<button name=action value={action}"
                f"{f' class={cls}' if cls else ''}>{label}</button></form>")

    def _fleet(self, conn):
        spent = conn.execute("SELECT COALESCE(SUM(tokens_in + tokens_out),0) s FROM agent_run"
                             " WHERE created_at > datetime('now', ?)",
                             (f"-{config.FLEET_SPEND_WINDOW_HOURS} hours",)).fetchone()["s"]
        cap = int(db.get_setting(conn, "fleet_budget_tokens", config.FLEET_BUDGET_TOKENS))
        halted = spent >= cap
        pct = min(100, round(spent / cap * 100)) if cap else 0
        banner = (
            f"<div class='spend{' halt' if halted else ''}'>"
            f"<span>tokens · last {config.FLEET_SPEND_WINDOW_HOURS}h</span>"
            f"<span class=bar><i style='width:{pct}%'></i></span>"
            f"<span><b>{spent:,}</b> / {cap:,}"
            f"{' — HALTED (over budget)' if halted else ''}</span></div>")

        cards = []
        for d in conn.execute("SELECT * FROM device ORDER BY name").fetchall():
            age = None
            if d["last_seen"]:
                age = conn.execute("SELECT (julianday('now')-julianday(?))*86400 a",
                                   (d["last_seen"],)).fetchone()["a"]
            online = age is not None and age < config.DEVICE_OFFLINE_SEC
            # a device is offline only when its own status is 'online' but its heartbeat is stale;
            # paused/draining are human-set states shown as-is.
            state = d["status"] if d["status"] != "online" else ("online" if online else "offline")
            caps = json.loads(d["capabilities"] or "[]")
            chips = ("".join(f"<span class=chip>{esc(c)}</span>" for c in caps)
                     or "<span class='chip none'>no capabilities</span>")
            seen = f"{round(age)}s ago" if age is not None else "never"
            cur = f"<span>running <b>#{esc(d['current_ticket'])}</b></span>" if d["current_ticket"] else ""
            # consolidated controls: a single Pause/Resume toggle, plus Drain while active.
            if d["status"] in ("paused", "draining"):
                ctl = self._devbtn(d["name"], "resume", "Resume", "go")
            else:
                ctl = (self._devbtn(d["name"], "pause", "Pause")
                       + self._devbtn(d["name"], "drain", "Drain", "warn"))
            cards.append(
                f"<div class='dev{' off' if state == 'offline' else ''}'>"
                f"<div class=dh><span class=dn>{esc(d['name'])}</span>"
                f"<span class='pill st-{esc(state)}'>{esc(state)}</span></div>"
                f"<div class=meta><span>seen <b>{esc(seen)}</b></span>{cur}</div>"
                f"<div class=caps>{chips}</div>"
                f"<form class=cedit method=post action=/device-caps>"
                f"<input type=hidden name=device value=\"{esc(d['name'])}\">"
                f"<input name=capabilities value=\"{esc(', '.join(caps))}\" "
                f"placeholder='dev, repos:*, heavy'><button>Save</button></form>"
                f"<div class=ctl>{ctl}</div></div>")

        grid = (f"<div class=grid>{''.join(cards)}</div>" if cards else
                "<p class=mut>No devices yet. Start a worker pointed at this hub, or pair one below.</p>")
        pair = ("<div class=pair><form method=post action=/device-pair>"
                "<b>Pair a new device</b><br>"
                "<input name=device placeholder='device name (e.g. mbp)' required> "
                "<button>Generate token</button>"
                "<div class=tip>Issues a one-time token to paste into that machine's worker Settings.</div>"
                "</form></div>")
        tip = ("<p class=tip>Capabilities gate which tickets a device claims (e.g. "
               "<code>dev</code>, <code>repos:*</code>, <code>market-data</code>). "
               "<b>Pause</b> stops it claiming (Resume to bring it back); "
               "<b>Drain</b> lets it finish its current ticket first.</p>")
        return FLEET_CSS + banner + grid + pair + tip

    def _log(self, conn):
        # Global view of the append-only audit trail: every message the hub handled
        # (claims, ops, decisions, device control, triage...), newest first. Same table
        # shape as a ticket's audit trail, plus a ticket link and a details expander for
        # the JSON detail column (argv, exit code, cost, score breakdown). Heartbeats are
        # not audited by design — they're liveness noise, not handled work.
        rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200").fetchall()
        out = ["<meta http-equiv=refresh content=3>"  # near-realtime without JS; LAN dashboard
               "<h1>Activity log</h1>"
               "<p class=mut>Last 200 events, newest first. Auto-refreshes every 3s. "
               "<a href=/log/raw>Raw API requests &rarr;</a></p>"
               "<table><tr><th>when</th><th>actor</th><th>action</th>"
               "<th>ticket</th><th>why</th></tr>"]
        for a in rows:
            tkt = f"<a href=/ticket/{a['ticket_id']}>#{a['ticket_id']}</a>" if a["ticket_id"] else ""
            why = esc(a["reason"])
            detail = a["detail"] or "{}"
            if detail not in ("{}", ""):
                try:
                    pretty = json.dumps(json.loads(detail), indent=2)
                except ValueError:
                    pretty = detail  # keep the page alive on a malformed row
                why += f"<details><summary class=mut>details</summary><pre>{esc(pretty)}</pre></details>"
            out.append(
                f"<tr><td class=mut>{esc(a['created_at'])}</td><td>{esc(a['actor'])}</td>"
                f"<td>{esc(a['action'])}</td><td>{tkt}</td><td>{why}</td></tr>")
        out.append("</table>")
        return "".join(out)

    def _raw_log(self, conn):
        # Raw HTTP messages the hub received at /api/* (heartbeats, claims, ops...) with the
        # response status. Its own page, not an expander on /log, so the 3s auto-refresh
        # doesn't collapse it while you watch. Rolling window of the last REQUEST_LOG_CAP.
        rows = conn.execute("SELECT * FROM request_log ORDER BY id DESC LIMIT 200").fetchall()
        out = ["<meta http-equiv=refresh content=3>"
               "<h1>Raw API requests</h1>"
               f"<p class=mut>Every message the hub received at /api/*, newest first "
               f"(includes heartbeats). Rolling window of the last {db.REQUEST_LOG_CAP}. "
               "<a href=/log>&larr; Activity log</a></p>"
               "<table><tr><th>when</th><th>device</th><th>method</th>"
               "<th>path</th><th>status</th></tr>"]
        for r in rows:
            out.append(
                f"<tr><td class=mut>{esc(r['created_at'])}</td><td>{esc(r['device'])}</td>"
                f"<td>{esc(r['method'])}</td><td>{esc(r['path'])}</td>"
                f"<td>{esc(r['status'])}</td></tr>")
        out.append("</table>")
        return "".join(out)

    def _ticket(self, conn, tid):
        t = conn.execute("SELECT * FROM ticket WHERE id=?", (tid,)).fetchone()
        if not t:
            return "<p>No such ticket.</p>"
        hs = json.dumps(db.hstate(t), indent=2)
        audit = conn.execute("SELECT * FROM audit WHERE ticket_id=? ORDER BY id", (tid,)).fetchall()
        sel = lambda name: "".join(
            f"<option {'selected' if t[name] == v else ''}>{v}</option>" for v in range(1, 6))
        rows = "".join(
            f"<tr><td class=mut>{esc(a['created_at'])}</td><td>{esc(a['actor'])}</td>"
            f"<td>{esc(a['action'])}</td><td>{esc(a['reason'])}</td></tr>" for a in audit)
        return (
            f"<h1>#{t['id']} {esc(t['title'])}</h1>"
            f"<p><span class=badge>{esc(t['type'])}</span> "
            f"<span class=s-{esc(t['status'])}>{esc(t['status'])}</span> / {esc(t['sub_stage'])} "
            f"&mdash; {esc(scoring.breakdown(t))}</p>"
            f"<p>{esc(t['body'])}</p>"
            + (f"<div class='card err'><b>&#9888; failed</b> "
               f"<form class=inline method=post action=/retry style='margin-left:.6rem'>"
               f"<input type=hidden name=ticket_id value={t['id']}><button>Retry</button></form> "
               f"<form class=inline method=post action=/dismiss>"
               f"<input type=hidden name=ticket_id value={t['id']}><button>Dismiss</button></form></div>"
               if t["status"] == "failed" else "")
            + f"<div class=card><form method=post action=/factors>"
            f"<input type=hidden name=ticket_id value={t['id']}>"
            f"impact <select name=impact>{sel('impact')}</select> "
            f"urgency <select name=urgency>{sel('urgency')}</select> "
            f"confidence <select name=confidence>{sel('confidence')}</select> "
            f"effort <select name=effort>{sel('effort')}</select> "
            f"<button>Rescore</button></form></div>"
            + f"<div class=card><form method=post action=/set-project>"
            f"<input type=hidden name=ticket_id value={t['id']}>"
            f"project <input name=project list=projects value=\"{esc(t['project'])}\""
            f" placeholder='project (optional)' size=22> <button>Save</button></form>"
            + self._datalist(self._projects(conn)) + "</div>"
            + f"<h3>handler state</h3><pre>{esc(hs)}</pre>"
            f"<h3>audit trail</h3><table><tr><th>when</th><th>actor</th><th>action</th><th>why</th></tr>"
            f"{rows}</table>")


def serve(port=8765):
    db.init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"outerloop UI on http://127.0.0.1:{port}")
    srv.serve_forever()
