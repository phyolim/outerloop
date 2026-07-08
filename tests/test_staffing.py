# Self-contained: per-project staffing — staffing.yml parse/save round-trip, the
# resolution precedence (staffing > glob > generalist), hub->worker inheritance,
# and the Projects/Agents JSON endpoints over real HTTP. Throwaway home. No deps.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-staffing-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
# --- test body ---
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from outerloop import config, db, personas
from outerloop.agent import run_agent
from outerloop.context import Ctx

config.AGENTS_DIR.mkdir(parents=True)
(config.AGENTS_DIR / "fintech-ux.md").write_text(
    "---\nname: fintech-ux\ndescription: finance specialist\nroles: author, fixer\n"
    "projects: banking-*\nmodel: haiku\n---\nMoney math is sacred.\n")
(config.AGENTS_DIR / "security-hawk.md").write_text(
    "---\nroles: reviewer\n---\nAssume every input is hostile.\n")

bank = {"project": "banking-app", "repo_path": None}

# --- 1. staffing.yml: save -> load round-trip; explicit staffing beats the glob;
#        clearing a slot falls back; a stale name falls through to the roster.
personas.save_assignment("Banking-App", "author", "security-hawk")
assert personas.load_staffing() == {"banking-app": {"author": "security-hawk"}}
p, why = personas.resolve("author", bank)
assert p["name"] == "security-hawk" and why == "project staffing: banking-app", (p, why)
# ...even though security-hawk's own roles say reviewer-only: explicit assignment wins.
personas.save_assignment("banking-app", "author", None)          # clear -> glob applies
assert personas.load_staffing() == {}
p, why = personas.resolve("author", bank)
assert p["name"] == "fintech-ux" and why == "persona glob banking-*", (p, why)
personas.save_assignment("banking-app", "author", "long-gone")   # stale name
p, why = personas.resolve("author", bank)
assert p["name"] == "fintech-ux", (p, why)                       # falls through, not None
personas.save_assignment("banking-app", "author", "security-hawk")

# hand-written keys survive a round-trip through a foreign entry
raw = config.STAFFING_FILE.read_text()
assert "banking-app:" in raw and "author: security-hawk" in raw, raw

# --- 2. fleet: staffing rides the heartbeat like the roster; hub owns it.
cfg = config.hub_cfg()
assert cfg["STAFFING"] == {"banking-app": {"author": "security-hawk"}}
config.apply_hub_cfg({"STAFFING": {"banking-app": {"author": "fintech-ux"}},
                      "PERSONAS": cfg["PERSONAS"]})
assert personas.resolve("author", bank)[0]["name"] == "fintech-ux"
config.HUB_STAFFING = config.HUB_PERSONAS = None                 # back to local files

# --- 3. run wiring: the persona lands on the agent_run ROW (not just audit), and
#        the audit reason carries the one-sentence why.
conn = db.init_db()
conn.execute("INSERT INTO ticket(title, body, type, project)"
             " VALUES('t','b','coding','banking-app')")
t = conn.execute("SELECT * FROM ticket WHERE id=1").fetchone()
run_agent(Ctx(conn, config, "tick-test"), "author", "do it", ticket_id=1, ticket=t)
row = conn.execute("SELECT * FROM agent_run WHERE ticket_id=1").fetchone()
assert row["persona"] == "security-hawk", dict(row)
aud = conn.execute("SELECT * FROM audit WHERE action='agent_run'").fetchone()
assert "as 'security-hawk' — project staffing: banking-app" in aud["reason"], aud["reason"]

# --- 4. the JSON seam, over real HTTP.
srv = ThreadingHTTPServer(("127.0.0.1", 0), __import__("outerloop.web", fromlist=["Handler"]).Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]


def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return json.loads(r.read())


def post(path, payload):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


agents = get("/ui/agents.json")
assert [a["name"] for a in agents["agents"]] == ["fintech-ux", "security-hawk"]
fin = agents["agents"][0]
assert "Money math is sacred." in fin["content"] and fin["file"] == "fintech-ux.md"
hawk = agents["agents"][1]
assert {"project": "banking-app", "role": "author"} in hawk["pairings"]
assert hawk["last_at"], hawk   # ran in step 3
assert agents["roles"] == ["groomer", "author", "reviewer", "fixer", "shipper"]

projects = get("/ui/projects.json")
proj = next(p for p in projects["projects"] if p["name"] == "banking-app")
assert proj["open"] == 1 and proj["coverage"] is True
assert proj["staffing"] == {"author": "security-hawk"}
assert proj["resolution"]["author"]["persona"] == "security-hawk"
assert proj["resolution"]["author"]["why"] == "project staffing: banking-app"
assert proj["resolution"]["reviewer"]["persona"] == "security-hawk"  # via its roles
assert proj["resolution"]["groomer"]["persona"] is None

code, _ = post("/ui/staffing-set", {"project": "banking-app", "role": "author", "persona": ""})
assert code == 200
assert get("/ui/projects.json")["projects"][0]["resolution"]["author"]["why"] \
    == "persona glob banking-*"
code, body = post("/ui/staffing-set", {"project": "banking-app", "role": "author",
                                       "persona": "nobody"})
assert code == 400 and "no persona" in body["error"]
code, body = post("/ui/staffing-set", {"project": "banking-app", "role": "sudo",
                                       "persona": "fintech-ux"})
assert code == 400

code, body = post("/ui/agent-save", {"file": "foodie.md",
                                     "content": "---\nroles: author\nprojects: food-*\n---\nDelight eaters.\n"})
assert code == 200 and body["loaded"] is True
assert "foodie" in [a["name"] for a in get("/ui/agents.json")["agents"]]
code, body = post("/ui/agent-save", {"file": "../evil.md", "content": "x"})
assert code == 400
code, body = post("/ui/agent-save", {"file": "README.md", "content": "x"})
assert code == 400

srv.shutdown()
conn.close()
print("ok: staffing precedence, yml round-trip, hub inheritance, projects/agents API")
