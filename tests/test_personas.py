# Self-contained: persona parsing, roster resolution, prompt/model/audit wiring,
# and hub->worker inheritance. Uses a throwaway roster dir + FAKE-mode DB. No deps.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-personas-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
# --- test body ---
import json

from outerloop import config, db, personas
from outerloop.agent import run_agent
from outerloop.context import Ctx

# AGENTS_DIR already lives under OUTERLOOP_HOME (= _TMP), so this is throwaway.
AGENTS = config.AGENTS_DIR
AGENTS.mkdir(parents=True)


def write(name, text):
    (AGENTS / name).write_text(text)


write("fintech-ux.md", """---
name: fintech-ux
description: finance specialist
roles: author, fixer
projects: banking-*, acme/ledger
model: haiku
---
You are a fintech specialist. Money math is sacred.
""")
write("foodie.md", """---
roles: author
projects: food-*
---
You are a food-delivery product engineer.
""")
write("generalist.md", """---
roles: author, reviewer
---
You are a pragmatic generalist.
""")
write("README.md", "not a persona")
write("_template.md", "---\nroles: author\n---\nskip me")
write("empty-body.md", "---\nroles: author\n---\n")

# --- 1. parsing: frontmatter fields, filename-default name, skip rules.
roster = personas.load_personas()
assert [p["name"] for p in roster] == ["fintech-ux", "foodie", "generalist"], roster
fin = roster[0]
assert fin["roles"] == ["author", "fixer"] and fin["model"] == "haiku"
assert fin["projects"] == ["banking-*", "acme/ledger"]
assert fin["body"].startswith("You are a fintech specialist")
assert roster[1]["name"] == "foodie"  # name defaults to the filename stem

# --- 2. resolution: explicit project match beats generalist; roles filter; globs
#        hit both the project label and repo_path; None when nothing fits.
bank = {"project": "Banking-App", "repo_path": None}
food = {"project": None, "repo_path": "https://github.com/me/food-orderer"}
other = {"project": "blog", "repo_path": None}
p, why = personas.resolve("author", bank)                            # case-insensitive glob
assert p["name"] == "fintech-ux" and why == "persona glob banking-*", (p, why)
assert personas.resolve("author", food)[0]["name"] == "foodie"       # matched via repo_path
p, why = personas.resolve("author", other)
assert p["name"] == "generalist" and why == "generalist"             # fallback
assert personas.resolve("reviewer", bank)[0]["name"] == "generalist" # fintech-ux can't review
assert personas.resolve("groomer", bank)[0] is None                  # nobody plays groomer
assert personas.resolve("author", None)[0] is None                   # no ticket context

# --- 3. model: persona wins over role routing (hub ships MODELS resolved, so a
#        worker can't tell routing from defaults); alias resolves to a full id.
assert config.resolve_model("author", persona_model="haiku") == config.MODEL_TIERS["haiku"]
assert config.resolve_model("author") == config.MODEL_TIERS["opus"]  # default untouched

# --- 4. run_agent: preamble lands in the recorded prompt, persona in the audit
#        detail, persona model on the agent_run row. FAKE mode, so no subprocess.
db.init_db()
conn = db.connect()
conn.execute("INSERT INTO ticket(title, body, type, project) VALUES('t','b','coding','banking-app')")
t = conn.execute("SELECT * FROM ticket WHERE id=1").fetchone()
run_agent(Ctx(conn, config, "tick-test"), "author", "do the thing", ticket_id=1, ticket=t)
row = conn.execute("SELECT * FROM agent_run WHERE ticket_id=1").fetchone()
assert row["prompt"].startswith("YOUR PERSONA (fintech-ux)"), row["prompt"][:80]
assert "Money math is sacred." in row["prompt"] and "do the thing" in row["prompt"]
assert row["model"] == config.MODEL_TIERS["haiku"], row["model"]
aud = conn.execute("SELECT * FROM audit WHERE action='agent_run'").fetchone()
assert "as 'fintech-ux'" in aud["reason"], aud["reason"]
assert json.loads(aud["detail"])["persona"] == "fintech-ux"

# ...and a ticket no persona covers runs exactly as before (no preamble, role model).
conn.execute("INSERT INTO ticket(title, body, type, project) VALUES('t2','b','coding','blog')")
t2 = conn.execute("SELECT * FROM ticket WHERE id=2").fetchone()
run_agent(Ctx(conn, config, "tick-test"), "groomer", "expand it", ticket_id=2, ticket=t2)
row2 = conn.execute("SELECT * FROM agent_run WHERE ticket_id=2").fetchone()
assert row2["prompt"] == "expand it" and row2["model"] == config.MODEL_TIERS["sonnet"]
conn.close()

# --- 5. fleet: hub_cfg carries the roster; a worker inherits it and prefers it over
#        local files; an empty hub roster means empty (hub owns it); an old hub
#        (no PERSONAS key) leaves local files in effect.
cfg = config.hub_cfg()
assert [p["name"] for p in cfg["PERSONAS"]] == ["fintech-ux", "foodie", "generalist"]
config.apply_hub_cfg({"PERSONAS": [
    {"name": "hub-only", "description": "", "roles": [], "projects": [], "model": "",
     "body": "You come from the hub."}]})
assert personas.resolve("author", bank)[0]["name"] == "hub-only"
config.apply_hub_cfg({"PERSONAS": []})
assert personas.resolve("author", bank)[0] is None       # hub says: no personas
config.HUB_PERSONAS = None
config.apply_hub_cfg({"FAKE": True})                     # old hub: key absent
assert personas.resolve("author", bank)[0]["name"] == "fintech-ux"

# --- 6. roster cache invalidates on edit (the hub serves this every heartbeat).
write("foodie.md", "---\nroles: author\nprojects: food-*\nname: foodie-2\n---\nNew voice.")
assert "foodie-2" in [p["name"] for p in personas.load_personas()]

print("ok: personas parse, resolve by role+project, wire into runs, inherit from hub")
