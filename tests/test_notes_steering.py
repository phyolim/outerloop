# Self-contained: runs from anywhere, uses a throwaway FAKE-mode DB. No env setup needed.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-test-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
from outerloop import db as _bootstrap_db
_bootstrap_db.init_db()
# --- test body ---

"""Operator notes must STEER, not just survive: every stage that builds an agent
prompt threads {"q": "(operator note)"} clarifications into it, so a comment left
mid-flight reaches the next Claude run no matter which stage the ticket is in.
(Persistence across worker writebacks is covered by test_notes.py.)"""
import json
from pathlib import Path
from outerloop import config, db
from outerloop.tick import run_tick

BASE = Path(config.HOME)
NOTE = "steer-me-7c4a1"
HS = {"clarifications": [{"q": "(operator note)", "a": NOTE}],
      "branch": "outerloop/ticket-1-abc", "worktree_path": "/tmp/x",
      "groom": {"acceptance_criteria": ["works"], "tasks": ["do it"]},
      "last_findings": ["fix the thing"], "review_round": 1}


def fresh(name):
    config.DB_PATH = BASE / f"{name}.db"
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    for suf in ("-wal", "-shm"):
        p = Path(str(config.DB_PATH) + suf)
        if p.exists():
            p.unlink()
    return db.init_db()


def prompt_of(c, role):
    row = c.execute("SELECT prompt FROM agent_run WHERE role=?", (role,)).fetchone()
    assert row, f"expected a {role} run"
    return row["prompt"]


CASES = [  # (ticket type, sub_stage to enter, agent role that must see the note)
    ("coding", "seed", "groomer"),
    ("coding", "groomed", "author"),
    ("coding", "reviewing", "reviewer"),
    ("coding", "fixing", "fixer"),
    ("coding", "resolving_conflicts", "fixer"),
    ("knowledge", "seed", "knowledge"),
]
for ttype, stage, role in CASES:
    c = fresh(f"t_{ttype}_{stage}")
    c.execute("INSERT INTO ticket(title,body,type,status,sub_stage,handler_state,score)"
              " VALUES('t','b',?,'active',?,?,10)", (ttype, stage, json.dumps(HS)))
    run_tick()
    assert NOTE in prompt_of(c, role), \
        f"{ttype}/{stage}: operator note must reach the {role} prompt"
    print(f"OK {ttype}/{stage} -> note steers the {role}")

# knowledge review reads the drafted artifact, so drive it through the draft stage.
c = fresh("t_knowledge_review")
c.execute("INSERT INTO ticket(title,body,type,status,sub_stage,handler_state,score)"
          " VALUES('t','b','knowledge','active','seed',?,10)", (json.dumps(HS),))
run_tick()  # seed -> drafted (writes the artifact)
run_tick()  # drafted -> reviewed
assert NOTE in prompt_of(c, "reviewer"), "knowledge review must see the note"
print("OK knowledge/drafted -> note steers the reviewer")

# No notes -> no OPERATOR NOTES block bloating the prompt.
c = fresh("t_no_note")
hs = dict(HS, clarifications=[])
c.execute("INSERT INTO ticket(title,body,type,status,sub_stage,handler_state,score)"
          " VALUES('t','b','coding','active','reviewing',?,10)", (json.dumps(hs),))
run_tick()
assert "OPERATOR NOTES" not in prompt_of(c, "reviewer"), "no header when there are no notes"
print("OK no notes -> no OPERATOR NOTES header")
