"""The permission bridge: a tiny stdio MCP server headless claude calls when a tool
needs a permission it doesn't have (--permission-prompt-tool, wired in agent._real).
Headless claude would otherwise auto-deny silently; this posts a 'permission' decision
into the ticket thread (Inbox + ntfy push), waits for the human's Allow/Deny, and
answers claude. The ticket is NOT blocked — the run stays live while it waits — and
an unanswered ask is voided (perm_expire) so the Inbox never collects dead asks.

Spawned by claude, configured entirely by env (set in agent._perm_args):
  OUTERLOOP_PERM_TICKET / OUTERLOOP_PERM_EPOCH  which leased run is asking
  OUTERLOOP_PERM_HUB [+ OUTERLOOP_PERM_TOKEN]   HTTP mode (any worker, incl. combined)
  neither                                       direct-DB mode (cron `outerloop tick`)
  OUTERLOOP_PERM_WAIT                           seconds before timeout -> deny
"""

import json
import os
import sys
import time

from . import __version__

POLL_SEC = 3

_TOOL = {
    "name": "approve",
    "description": "Ask the outerloop operator (in the ticket thread) to allow or deny"
                   " a tool call that needs permission.",
    "inputSchema": {"type": "object",
                    "properties": {"tool_name": {"type": "string"},
                                   "input": {"type": "object"}},
                    "additionalProperties": True},
}


def env_cfg():
    tid = os.environ.get("OUTERLOOP_PERM_TICKET")
    ep = os.environ.get("OUTERLOOP_PERM_EPOCH")
    return {"ticket": int(tid) if tid else None,
            "epoch": int(ep) if ep else None,
            "hub": os.environ.get("OUTERLOOP_PERM_HUB"),
            "token": os.environ.get("OUTERLOOP_PERM_TOKEN"),
            "wait": int(os.environ.get("OUTERLOOP_PERM_WAIT") or 180)}


def _op(cfg, op, **kw):
    """Run a named write op: over HTTP through the hub's epoch-fenced /api/op seam
    when a hub URL is configured, else directly against the local DB (cron tick)."""
    kw.update(ticket_id=cfg["ticket"], epoch=cfg["epoch"])
    if cfg["hub"]:
        from . import client
        return client.post(cfg["hub"], "/api/op", {"op": op, **kw}, token=cfg["token"])
    from . import context, db
    conn = db.connect()
    try:
        return context.apply_op(conn, op, kw, tick_id="perm")
    finally:
        conn.close()


def _status(cfg, did):
    if cfg["hub"]:
        from . import client
        return client.get(cfg["hub"], f"/api/decision/{did}", token=cfg["token"])
    from . import db
    conn = db.connect()
    try:
        row = conn.execute("SELECT status, rework, answer_note FROM decision WHERE id=?",
                           (did,)).fetchone()
        return {k: row[k] for k in row.keys()} if row else None
    finally:
        conn.close()


def decide(args, cfg, sleep=time.sleep, clock=time.monotonic):
    """Post the ask, poll for the human's answer, return claude's permission verdict
    ({"behavior": "allow"|"deny", ...}). Every failure path denies — the bridge must
    never allow by accident, and never crash the run."""
    tool = str(args.get("tool_name") or "?")
    tin = args.get("input") if isinstance(args.get("input"), dict) else {}
    if not cfg["ticket"]:
        return {"behavior": "deny", "message": "no ticket context; denied"}
    q = f"The agent asks permission to use `{tool}` with: {json.dumps(tin)[:600]}"
    try:
        did = _op(cfg, "perm_ask", question=q,
                  context=json.dumps({"tool_name": tool}))["decision_id"]
    except Exception as e:  # noqa: BLE001 — hub down / lease reclaimed / anything
        return {"behavior": "deny",
                "message": f"permission bridge could not reach the hub ({e}); denied"}
    deadline = clock() + cfg["wait"]
    while clock() < deadline:
        sleep(POLL_SEC)
        try:
            d = _status(cfg, did)
        except Exception:  # noqa: BLE001 — transient hub blip: keep polling
            continue
        if not d:
            break  # decision vanished (janitor/close) — fall through to deny
        if d["status"] == "approved":
            return {"behavior": "allow", "updatedInput": tin}
        if d["status"] == "rejected":
            note = (d.get("answer_note") or "").strip()
            return {"behavior": "deny",
                    "message": "denied by the operator" + (f": {note}" if note else "")}
    try:
        _op(cfg, "perm_expire", decision_id=did,
            note=f"(expired — no answer within {cfg['wait']}s; the tool call was denied)")
    except Exception:  # noqa: BLE001 — the janitor sweep catches what this misses
        pass
    return {"behavior": "deny",
            "message": (f"No operator answer within {cfg['wait']}s — the call was denied."
                        " Do not retry it. If the task cannot proceed without it, stop and"
                        ' respond with ONLY {"question": "<what you need permission for'
                        ' and why>"}.')}


def _reply(mid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
    sys.stdout.flush()


def main():
    """Newline-delimited JSON-RPC over stdio — the minimum of MCP that
    --permission-prompt-tool needs: initialize, tools/list, tools/call."""
    cfg = env_cfg()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            proto = (msg.get("params") or {}).get("protocolVersion") or "2024-11-05"
            _reply(mid, {"protocolVersion": proto, "capabilities": {"tools": {}},
                         "serverInfo": {"name": "outerloop-permission",
                                        "version": __version__}})
        elif method == "tools/list":
            _reply(mid, {"tools": [_TOOL]})
        elif method == "tools/call":
            args = (msg.get("params") or {}).get("arguments") or {}
            _reply(mid, {"content": [{"type": "text",
                                      "text": json.dumps(decide(args, cfg))}]})
        elif mid is not None:  # ping / anything else that expects an answer
            _reply(mid, {})


if __name__ == "__main__":
    main()
