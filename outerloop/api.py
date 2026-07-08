"""The hub's JSON API. handle() returns (status, dict); the hub's
HTTP handler serializes it. The /api/op endpoint is the remote seam: it runs the
same write op the local helpers run, epoch-fenced inside the op's transaction."""

import json

from . import __version__, auth, claim, config, context, db, git_ops, pairing, taxonomy


def handle(method, path, body, conn, auth_worker=None, intake_token=None):
    body = body or {}
    if method == "POST" and path == "/api/intake":
        return _intake(conn, body, intake_token)
    if method == "POST" and path == "/api/heartbeat":
        if not _worker_ok(conn, body, auth_worker):
            return 403, {"error": "token does not match worker"}
        return _heartbeat(conn, body)
    if method == "POST" and path == "/api/claim":
        if not _worker_ok(conn, body, auth_worker):
            return 403, {"error": "token does not match worker"}
        return 200, (claim.claim(conn, body["worker"]) or {})
    if method == "POST" and path == "/api/op":
        return _op(conn, body, auth_worker)
    if method == "POST" and path == "/api/release":
        return 200, claim.release(conn, int(body["ticket_id"]), int(body["epoch"]))
    if method == "POST" and path == "/api/lease/renew":
        r = claim.renew(conn, int(body["ticket_id"]), int(body["epoch"]))
        return (200, r) if r else (409, {"error": "stale epoch on renew"})
    if method == "POST" and path == "/api/tickets":
        return _create_ticket(conn, body)
    if method == "GET" and path.startswith("/api/ticket/"):
        row = conn.execute("SELECT * FROM ticket WHERE id=?", (int(path.rsplit("/", 1)[-1]),)).fetchone()
        return (200, _rowdict(row)) if row else (404, {"error": "no such ticket"})
    if method == "GET" and path == "/api/fleet":
        return 200, _fleet(conn)
    if method == "GET" and path == "/api/tasks":
        return 200, _tasks(conn)
    if method == "GET" and path == "/api/decisions":
        return 200, _decisions(conn)
    if method == "POST" and path == "/api/pair-confirm":
        # NOT under /api/pair/ on purpose: that prefix is the un-authenticated
        # worker seam — confirming a code is an operator action and stays behind
        # the bearer gate (the hub's own menu-bar app pairs from its popover).
        ok, err = pairing.confirm(conn, str(body.get("request_id") or ""),
                                  str(body.get("code") or ""))
        return (200, {"ok": True}) if ok else (400, {"error": err})
    if method == "POST" and path == "/api/kill":
        on = bool(body.get("on"))
        db.set_setting(conn, "kill_switch", "on" if on else "off")
        db.append_audit(conn, "human", "kill_switch",
                        f"kill switch turned {'on' if on else 'off'} via menu bar")
        return 200, {"ok": True, "on": on}
    if method == "POST" and path.startswith("/api/tasks/") and path.endswith("/terminate"):
        return _terminate(conn, int(path[len("/api/tasks/"):-len("/terminate")]))
    if method == "POST" and path.startswith("/api/worker/") and path.endswith("/control"):
        return _control(conn, path[len("/api/worker/"):-len("/control")], body)
    return 404, {"error": "not found"}


def _rowdict(row):
    return {k: row[k] for k in row.keys()}


def _worker_ok(conn, body, auth_worker):
    """When auth is on, a request acting as a worker must carry that worker's token."""
    return not auth.required(conn) or body.get("worker") == auth_worker


def _heartbeat(conn, body):
    name = body["worker"]
    with db.immediate(conn):
        if conn.execute("SELECT 1 FROM worker WHERE name=?", (name,)).fetchone():
            # Capabilities are HUB-owned once a worker is registered: the fleet UI is the
            # source of truth for what work a machine takes, so a heartbeat must NOT clobber
            # them (that would silently revert whatever a human assigned). Only liveness
            # fields refresh here; caps change only via /api/worker/<name>/control set_caps.
            conn.execute("UPDATE worker SET version=?, current_ticket=?,"
                         " last_seen=datetime('now') WHERE name=?",
                         (body.get("version"), body.get("current_ticket"), name))
        else:
            # First registration seeds caps from what the worker baked (--caps) — just an
            # initial default the human can then edit in the fleet UI.
            conn.execute("INSERT INTO worker(name, capabilities, version, last_seen)"
                         " VALUES(?,?,?, datetime('now'))",
                         (name, json.dumps(body.get("capabilities", [])), body.get("version")))
            db.append_audit(conn, "fleet", "worker_registered",
                            f"{name} joined: {body.get('capabilities')}")
        d = conn.execute("SELECT status, target_ticket FROM worker WHERE name=?",
                         (name,)).fetchone()
    return 200, {"status": d["status"], "target_ticket": d["target_ticket"],
                 "hub_version": __version__, "cfg": config.hub_cfg()}


def _op(conn, body, auth_worker=None):
    op = body.pop("op", None)
    if op not in context._LOCAL_OPS:
        return 400, {"error": f"unknown op {op!r}"}
    # When auth is on, a fenced write must come from the worker that HOLDS the lease.
    # The epoch alone isn't a secret (GET /api/ticket exposes claim_epoch), so the
    # fence stops stale writers but not wrong ones — this binds op to claimer.
    if auth.required(conn) and body.get("ticket_id") is not None and body.get("epoch") is not None:
        lease = conn.execute("SELECT worker FROM lease WHERE ticket_id=?",
                             (body["ticket_id"],)).fetchone()
        if not lease:
            # Lease already reclaimed (not yet re-claimed) — same outcome as a stale
            # epoch, so return 409 and let the worker's StaleEpoch path abandon cleanly.
            return 409, {"error": "lease reclaimed; op rejected"}
        if lease["worker"] != auth_worker:
            return 403, {"error": "op not from the lease-holding worker"}
    try:
        return 200, context.apply_op(conn, op, body)
    except context.StaleEpoch as e:
        return 409, {"error": str(e)}


def _create_ticket(conn, body):
    dk = body.get("dedup_key")
    req = body.get("requires")
    kind = taxonomy.normalize_kind(body.get("kind"), body.get("type"))
    type_ = taxonomy.type_for(kind)
    repo, rerr = git_ops.normalize_repo_path(body.get("repo_path"))
    if rerr:
        return 400, {"error": rerr}
    with db.immediate(conn):
        # Dedup check INSIDE the write transaction: concurrent webhook re-deliveries
        # (threaded hub) must not both pass a check-then-insert race.
        if dk:
            ex = conn.execute("SELECT id FROM ticket WHERE dedup_key=?", (dk,)).fetchone()
            if ex:
                return 200, {"id": ex["id"], "dedup": True}
        cur = conn.execute(
            "INSERT INTO ticket(title, body, type, kind, requires, prefer, pin, repo_path, project, dedup_key, draft)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (body.get("title") or "", body.get("body") or "", type_, kind,
             json.dumps(req) if req is not None else "[]",
             body.get("prefer"), body.get("pin"), repo, body.get("project"), dk,
             1 if body.get("draft") else 0))
        tid = cur.lastrowid
        db.append_audit(conn, body.get("source", "api"), "created",
                        "ticket created via API", ticket_id=tid)
    return 200, {"id": tid}


def _intake(conn, body, supplied_token):
    """External intake (Sentry webhook, phone shortcut, any curl) -> a ticket. Self-
    authenticated by a shared secret (settings.intake_token) so webhook senders don't
    need a worker bearer token; unset token = endpoint off (default-deny)."""
    expected = db.get_setting(conn, "intake_token", "")
    if not expected:
        return 403, {"error": "intake disabled: set intake_token first"}
    import hmac
    if not supplied_token or not hmac.compare_digest(supplied_token, expected):
        return 403, {"error": "bad intake token"}
    mapped = _intake_map(body)
    if not (mapped.get("title") or "").strip():
        return 400, {"error": "no title in payload"}
    mapped["source"] = "intake"
    return _create_ticket(conn, mapped)


def _intake_map(body):
    """Normalize a payload into _create_ticket fields. Two shapes: our generic JSON
    (passed through) and a Sentry issue-alert webhook (title/permalink extracted,
    issue id as dedup_key so alert re-fires don't flood the queue)."""
    issue = (body.get("data") or {}).get("issue") if isinstance(body.get("data"), dict) else None
    if issue:  # Sentry issue alert
        link = issue.get("permalink") or ""
        culprit = issue.get("culprit") or ""
        return {"title": issue.get("title", ""), "kind": "bug",
                "body": "\n".join(x for x in (culprit, link) if x),
                "dedup_key": f"sentry-{issue.get('id')}" if issue.get("id") else None,
                "project": body.get("project"), "draft": body.get("draft")}
    return {k: body.get(k) for k in ("title", "body", "kind", "project", "repo_path",
                                     "requires", "prefer", "dedup_key", "draft")}


def _fleet(conn):
    out = []
    for d in conn.execute("SELECT * FROM worker ORDER BY name").fetchall():
        age = None
        if d["last_seen"]:
            age = conn.execute("SELECT (julianday('now')-julianday(?))*86400 a",
                               (d["last_seen"],)).fetchone()["a"]
        out.append({"name": d["name"], "capabilities": json.loads(d["capabilities"] or "[]"),
                    "status": d["status"],
                    "current_ticket": d["current_ticket"], "last_seen": d["last_seen"],
                    "seconds_ago": round(age) if age is not None else None,
                    "online": age is not None and age < config.WORKER_OFFLINE_SEC})
    # spend + kill switch ride along so the menu-bar popover needs no extra round-trips
    spent = conn.execute("SELECT COALESCE(SUM(tokens_in + tokens_out),0) s FROM agent_run"
                         " WHERE created_at > datetime('now', ?)",
                         (f"-{config.FLEET_SPEND_WINDOW_HOURS} hours",)).fetchone()["s"]
    cap = int(db.get_setting(conn, "fleet_budget_tokens", config.FLEET_BUDGET_TOKENS))
    return {"workers": out, "spend": {"spent": spent, "cap": cap},
            "kill_switch": db.get_setting(conn, "kill_switch", "off") == "on",
            "pairing": pairing.pending()}


def _tasks(conn):
    """Live task list for the fleet UI: the meaningful (non-inbox, non-done) tickets,
    each joined to its lease (which worker runs it now) and its most recent agent_run
    (session_id + worktree, so the app can find the transcript / kill the process).
    Running tasks first."""
    rows = conn.execute(
        "SELECT t.id, t.title, t.type, t.status, t.sub_stage, t.score,"
        "       l.worker AS worker,"
        "       (SELECT session_id FROM agent_run WHERE ticket_id=t.id"
        "         ORDER BY created_at DESC, rowid DESC LIMIT 1) AS session_id,"
        "       (SELECT worktree_path FROM agent_run WHERE ticket_id=t.id"
        "         ORDER BY created_at DESC, rowid DESC LIMIT 1) AS worktree_path"
        "  FROM ticket t LEFT JOIN lease l ON l.ticket_id=t.id"
        " WHERE t.status IN ('active','blocked','parked')"
        " ORDER BY (l.worker IS NOT NULL) DESC, t.score IS NULL, t.score DESC,"
        "          t.updated_at DESC LIMIT 100").fetchall()
    return {"tasks": [{k: r[k] for k in r.keys()} | {"running": r["worker"] is not None}
                      for r in rows]}


def _decisions(conn):
    """Everything waiting on a human, for the menu-bar popover's NEEDS YOU list:
    pending decisions plus failed tickets. Titles only — the dashboard has the detail."""
    out = [{"ticket_id": r["tid"], "title": r["title"],
            "kind": "question" if r["kind"] == "clarification" else r["kind"],
            "decision_id": r["decision_id"], "at": r["at"]}
           for r in conn.execute(
               "SELECT d.id AS decision_id, d.kind, d.created_at AS at,"
               " t.id AS tid, t.title FROM decision d JOIN ticket t ON t.id=d.ticket_id"
               " WHERE d.status='pending' ORDER BY d.created_at").fetchall()]
    out += [{"ticket_id": r["id"], "title": r["title"], "kind": "error",
             "decision_id": None, "at": r["updated_at"]}
            for r in conn.execute("SELECT id, title, updated_at FROM ticket"
                                  " WHERE status='failed' ORDER BY updated_at DESC").fetchall()]
    return {"decisions": out}


def _terminate(conn, tid):
    """Human-triggered stop of one ticket. Fences any in-flight worker (bump claim_epoch
    -> its next write raises StaleEpoch and it abandons the stage), drops the lease, and
    parks the ticket so it isn't re-claimed. The app kills the local claude process; this
    is the hub-side half. Revive from the Parked page."""
    with db.immediate(conn):
        if not conn.execute("SELECT 1 FROM ticket WHERE id=?", (tid,)).fetchone():
            return 404, {"error": "no such ticket"}
        conn.execute("UPDATE ticket SET claim_epoch=claim_epoch+1, status='parked',"
                     " park_reason='terminated by user', assigned_worker=NULL,"
                     " updated_at=datetime('now') WHERE id=?", (tid,))
        conn.execute("DELETE FROM lease WHERE ticket_id=?", (tid,))
        db.append_audit(conn, "human", "terminated", f"ticket {tid} terminated by user",
                        ticket_id=tid, to_stage="parked")
    return 200, {"ok": True}


def _control(conn, name, body):
    action = body.get("action")
    mapping = {"pause": "paused", "resume": "online", "drain": "draining"}
    with db.immediate(conn):
        if action in mapping:
            conn.execute("UPDATE worker SET status=? WHERE name=?", (mapping[action], name))
        elif action == "target":
            conn.execute("UPDATE worker SET target_ticket=? WHERE name=?",
                         (body.get("ticket_id"), name))
        elif action == "set_caps":
            # The fleet UI (or a client) assigns what work this worker claims. Stored as a
            # JSON array; claim.py reads it as the authoritative capability set.
            caps = body.get("capabilities") or []
            conn.execute("UPDATE worker SET capabilities=? WHERE name=?",
                         (json.dumps(caps), name))
            db.append_audit(conn, "human", "worker_control", f"{name}: set_caps {caps}")
            return 200, {"ok": True, "capabilities": caps}
        elif action == "rename":
            new = (body.get("new_name") or "").strip()[:32]
            if not new:
                return 400, {"error": "new_name required"}
            if new == name:
                return 200, {"ok": True, "worker": new}
            if not conn.execute("SELECT 1 FROM worker WHERE name=?", (name,)).fetchone():
                return 404, {"error": f"no such worker {name!r}"}
            if conn.execute("SELECT 1 FROM worker WHERE name=?", (new,)).fetchone():
                return 409, {"error": f"a worker named {new!r} already exists"}
            # worker.name is referenced by plain TEXT columns (no FK cascade) —
            # carry the leases, assignments, and pins along with the row.
            conn.execute("UPDATE worker SET name=? WHERE name=?", (new, name))
            conn.execute("UPDATE lease SET worker=? WHERE worker=?", (new, name))
            conn.execute("UPDATE ticket SET assigned_worker=? WHERE assigned_worker=?",
                         (new, name))
            conn.execute("UPDATE ticket SET pin=? WHERE pin=?", (new, name))
            db.append_audit(conn, "human", "worker_control", f"{name}: renamed to {new}")
            return 200, {"ok": True, "worker": new}
        elif action == "delete":
            if not conn.execute("SELECT 1 FROM worker WHERE name=?", (name,)).fetchone():
                return 404, {"error": f"no such worker {name!r}"}
            # Fence any in-flight ticket (epoch bump -> the removed worker's next
            # write is stale and abandoned) and free its lease so the work re-routes.
            for r in conn.execute("SELECT ticket_id FROM lease WHERE worker=?",
                                  (name,)).fetchall():
                conn.execute("UPDATE ticket SET claim_epoch=claim_epoch+1,"
                             " assigned_worker=NULL WHERE id=?", (r["ticket_id"],))
                db.append_audit(conn, "human", "lease_reclaimed",
                                f"lease freed: worker {name} removed from fleet",
                                ticket_id=r["ticket_id"])
            conn.execute("DELETE FROM lease WHERE worker=?", (name,))
            # A pin means "run only on this machine" — park those tickets (revivable)
            # rather than silently letting them route anywhere.
            for r in conn.execute("SELECT id FROM ticket WHERE status='active' AND pin=?",
                                  (name,)).fetchall():
                conn.execute("UPDATE ticket SET status='parked',"
                             " park_reason='pinned worker removed',"
                             " updated_at=datetime('now') WHERE id=?", (r["id"],))
                db.append_audit(conn, "human", "parked",
                                f"pinned worker '{name}' removed from fleet",
                                ticket_id=r["id"], to_stage="parked")
            # Dropping the row also drops token_hash — the bearer token is revoked.
            conn.execute("DELETE FROM worker WHERE name=?", (name,))
            db.append_audit(conn, "human", "worker_control",
                            f"{name}: removed from fleet, token revoked")
            return 200, {"ok": True}
        else:
            return 400, {"error": f"unknown action {action!r}"}
        db.append_audit(conn, "human", "worker_control", f"{name}: {action}")
    return 200, {"ok": True}
