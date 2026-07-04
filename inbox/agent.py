"""The single boundary to the worker brain (headless `claude -p`). Everywhere else
treats this as 'give a role + prompt, get structured data back'. The session id is
ASSIGNED here (not parsed) so author != reviewer is structural. FAKE mode returns
canned data so the whole orchestration spine runs with no external dependency."""

import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
import uuid


def _fake(role, prompt):
    """Deterministic canned responses, one per role, enough to walk every stage."""
    if role == "triage":
        return {"keep": True, "reason": "looks actionable (fake)"}
    if role == "scorer":
        return {"impact": 4, "urgency": 3, "confidence": 4, "effort": 2,
                "reversibility": "reversible", "justification": "fake estimate"}
    if role == "groomer":
        return {"tasks": ["Implement the change", "Add a test"],
                "acceptance_criteria": ["behavior X holds", "tests pass"],
                "summary": "groomed into tasks + acceptance criteria (fake)"}
    if role == "author":
        # A ticket whose body asks to CLARIFY pauses once for a human answer, then (seeing
        # the threaded-back answer) proceeds — so the clarification loop is exercisable.
        if "CLARIFY" in prompt and "EARLIER CLARIFICATIONS" not in prompt:
            return {"question": "Which datastore should this target — Postgres or SQLite?",
                    "summary": "blocked pending clarification (fake)"}
        return {"summary": "implemented feature (fake)",
                "files_changed": ["src/feature.py"], "diff_stat": "+24 -3"}
    if role == "reviewer":
        # Key off the round the handler embeds so the review<->fix loop exercises.
        m = re.search(r"ROUND:\s*(\d+)", prompt)
        rnd = int(m.group(1)) if m else 99
        if rnd == 0:
            return {"verdict": "request_changes",
                    "findings": ["edge case Y unhandled", "missing test for Z"]}
        return {"verdict": "approve", "findings": []}
    if role == "fixer":
        return {"summary": "addressed review findings (fake)"}
    if role == "knowledge":
        return {"deliverable": "# Research notes\n\nKey finding (fake).\n",
                "summary": "draft produced (fake)"}
    if role == "ops":
        return {"action": {"kind": "email", "to": "someone@example.com",
                           "subject": "Re: your request", "body": "drafted reply (fake)"},
                "summary": "drafted an external action (fake)"}
    return {"summary": f"fake {role}"}


# The JSON shape each role must return. Real claude gets this appended to its prompt;
# FAKE returns canned dicts so it never needs it. Keyed by ROLE (always present) rather
# than the optional json_schema arg, which callers set inconsistently.
ROLE_SCHEMAS = {
    "triage": '{"keep": true, "reason": "<one line>"}',
    "scorer": '{"impact": 3, "urgency": 3, "confidence": 3, "effort": 3,'
              ' "reversibility": "reversible", "justification": "<one line>"}',
    "groomer": '{"tasks": ["..."], "acceptance_criteria": ["..."], "summary": "<one line>"}',
    "author": '{"summary": "<one line>", "files_changed": ["..."], "diff_stat": "+N -M"}'
              '\nOR, if a requirement is ambiguous and you cannot proceed safely, respond'
              ' with ONLY {"question": "<the one thing you need the human to clarify>"}.',
    "reviewer": '{"verdict": "approve", "findings": ["..."]}',
    "fixer": '{"summary": "<one line>"}',
    "knowledge": '{"deliverable": "<markdown body>", "summary": "<one line>"}',
    "ops": '{"action": {"kind": "email", "to": "...", "subject": "...", "body": "..."},'
           ' "summary": "<one line>"}',
}


def _schema_suffix(role):
    schema = ROLE_SCHEMAS.get(role)
    if not schema:
        return ""
    return ("\n\nWhen done, respond with ONLY a single JSON object — no prose before or "
            "after, no code fences — matching exactly this shape:\n" + schema)


def _extract_json(text):
    """Pull the JSON object out of a claude result: verbatim, fenced, or embedded in
    prose. Returns None if nothing parses."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        brace = t.find("{")
        if brace != -1:
            t = t[brace:]
    try:
        return json.loads(t)
    except (json.JSONDecodeError, TypeError):
        pass
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(t[i:j + 1])
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _kill_group(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # kill the whole group, not orphan children
    except ProcessLookupError:
        pass
    proc.wait()  # not communicate(): the pump threads own the pipes and drain them to EOF


def _dispatch_event(env, on_event, state):
    """Map one stream-json envelope to displayable events; capture the final result.
    `state`: {"texts": [...accumulated assistant text...], "result": final envelope}."""
    t = env.get("type")
    if t == "assistant":
        for blk in (env.get("message") or {}).get("content") or []:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "text" and (blk.get("text") or "").strip():
                state["texts"].append(blk["text"])
                on_event("text", blk["text"])
            elif blk.get("type") == "tool_use":
                on_event("tool", f"{blk.get('name', '?')} {json.dumps(blk.get('input') or {})}")
    elif t == "user":
        for blk in (env.get("message") or {}).get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "tool_result":
                c = blk.get("content")
                if isinstance(c, list):
                    c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict))
                on_event("tool_result", str(c or ""))
    elif t == "result":
        state["result"] = env


def _real(cfg, role, prompt, cwd, allowed_tools, session_id, on_event=lambda k, b: None):
    """Shell headless claude, streaming events (--output-format stream-json) through
    on_event as they happen so the UI can show work in flight. Best-effort JSON parse
    of the result text. The hard per-run bound is a wall-clock deadline enforced on
    the line reader; accounting is in tokens."""
    argv = [cfg.CLAUDE_BIN, "-p", "--print", "--verbose",
            "--output-format", "stream-json",
            "--model", cfg.resolve_model(role),
            "--session-id", session_id,
            "--permission-mode", "acceptEdits"]
    if cwd:
        argv += ["--add-dir", str(cwd)]
    if allowed_tools:
        argv += ["--allowedTools", allowed_tools]
    # The prompt goes on STDIN, not as a positional arg: claude's --allowedTools and
    # --add-dir are variadic (<tools...>) and would swallow a trailing positional prompt.
    proc = subprocess.Popen(argv, cwd=str(cwd) if cwd else None,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    # Pump all three pipes on threads (the old communicate() did the same internally):
    # stdout/stderr so neither can fill and stall claude, stdin so a prompt bigger than
    # the pipe buffer fed to a hung claude can't block this thread past the deadline.
    def _feed_stdin():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # claude died at startup; the read loop below surfaces it
    lines, err_buf = queue.Queue(), []
    threading.Thread(target=_feed_stdin, daemon=True).start()
    threading.Thread(target=lambda: ([lines.put(ln) for ln in proc.stdout], lines.put(None)),
                     daemon=True).start()
    threading.Thread(target=lambda: err_buf.extend(proc.stderr), daemon=True).start()

    deadline = time.monotonic() + cfg.AGENT_TIMEOUT_BY_ROLE.get(role, cfg.AGENT_TIMEOUT_SEC)
    state = {"texts": [], "result": None}
    while True:
        try:
            ln = lines.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:  # deadline hit mid-run
            _kill_group(proc)
            # Charge worst-case so the budget halt still triggers on a killed run (must-fix #5).
            return {"data": {}, "text": "", "tokens_in": 0,
                    "tokens_out": cfg.TIMEOUT_CHARGE_TOKENS,
                    "exit_code": None, "timed_out": True}
        if ln is None:  # stdout EOF — claude is done
            break
        try:
            _dispatch_event(json.loads(ln), on_event, state)
        except (json.JSONDecodeError, TypeError):
            continue
    try:
        proc.wait(timeout=max(1.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        _kill_group(proc)

    env = state["result"] or {}
    usage = env.get("usage") or {}
    # Cache READS excluded: near-free, would swamp the budget signal.
    tin = int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
    tout = int(usage.get("output_tokens") or 0)
    text = env["result"] if isinstance(env.get("result"), str) else "\n".join(state["texts"])
    data = _extract_json(text)
    if data is None:
        data = {"text": text}
        if state["result"] is None:  # no result envelope: claude crashed mid-stream
            data["stderr"] = "".join(err_buf)
    return {"data": data, "text": text, "tokens_in": tin, "tokens_out": tout,
            "exit_code": proc.returncode, "timed_out": False}


def run_agent(ctx, role, prompt, *, ticket_id, cwd=None, allowed_tools=None,
              json_schema=None, session_id=None, worktree_path=None):
    """Run one headless agent. Records an agent_run row + audit and returns
    {session_id, data, tokens_in, tokens_out, exit_code, timed_out}."""
    cfg = ctx.cfg
    session_id = session_id or str(uuid.uuid4())  # claude requires a canonical dashed UUID
    model = cfg.resolve_model(role)

    def emit(kind, body):
        # Live-feed rows are best-effort visibility: a hub blip must not kill a
        # long run, and the epoch fence still guards the authoritative end-of-run
        # write below.
        try:
            ctx.write("agent_event", ticket_id=ticket_id, session_id=session_id,
                      role=role, kind=kind, body=body[:1500])
        except Exception:
            pass

    if cfg.FAKE:
        emit("text", f"working on it ({role}, fake)")
        res = {"data": _fake(role, prompt), "text": "", "tokens_in": 0, "tokens_out": 0,
               "exit_code": 0, "timed_out": False}
    else:
        res = _real(cfg, role, prompt + _schema_suffix(role), cwd, allowed_tools, session_id,
                    on_event=emit)

    tokens = res["tokens_in"] + res["tokens_out"]
    ctx.write("agent_run", session_id=session_id, ticket_id=ticket_id, role=role, prompt=prompt,
              model=model, worktree_path=str(worktree_path) if worktree_path else None,
              exit_code=res["exit_code"], timed_out=1 if res["timed_out"] else 0,
              output_json=json.dumps(res["data"]),
              tokens_in=res["tokens_in"], tokens_out=res["tokens_out"],
              actor=f"agent:{role}",
              reason=("timed out" if res["timed_out"] else "completed")
                     + f" ({tokens:,} tok, {model})",
              detail={"role": role, "session_id": session_id, "model": model,
                      "timed_out": res["timed_out"],
                      "tokens_in": res["tokens_in"], "tokens_out": res["tokens_out"]})
    res["session_id"] = session_id
    return res
