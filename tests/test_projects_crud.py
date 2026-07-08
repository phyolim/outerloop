# Self-contained: project CRUD over real HTTP — create an empty project, rename it
# (cascading to ticket labels + staffing), edit its repo, and delete it (unfiling
# tickets). Throwaway home. No deps.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-projcrud-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
# --- test body ---
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from outerloop import config, db, personas

config.AGENTS_DIR.mkdir(parents=True)
conn = db.init_db()

srv = ThreadingHTTPServer(("127.0.0.1", 0), __import__("outerloop.web", fromlist=["Handler"]).Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]


def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return json.loads(r.read())


def post(path, payload):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def names():
    return [p["name"] for p in get("/ui/projects.json")["projects"]]


# --- 1. create: an empty project exists with zero tickets and carries a repo.
code, body = post("/ui/project-create", {"name": "Atlas", "repo": ""})
assert code == 200 and body["name"] == "Atlas", body
assert "Atlas" in names()
proj = next(p for p in get("/ui/projects.json")["projects"] if p["name"] == "Atlas")
assert proj["open"] == 0, proj

# blank name is rejected; a duplicate (case-insensitive) is a 409.
assert post("/ui/project-create", {"name": "  ", "repo": ""})[0] == 400
assert post("/ui/project-create", {"name": "atlas", "repo": ""})[0] == 409

# --- 2. staff + label the project, then rename: both must follow.
conn.execute("INSERT INTO ticket(title, body, type, project) VALUES('t','b','coding','Atlas')")
conn.commit()
personas.save_assignment("Atlas", "author", None)  # no persona -> no-op, just proves key path
code, _ = post("/ui/staffing-set", {"project": "Atlas", "role": "author", "persona": ""})
assert code == 200
# give it a real staffing entry to prove the rename moves it
config.AGENTS_DIR.joinpath("dev.md").write_text("---\nname: dev\nroles: author\n---\nx\n")
assert post("/ui/staffing-set", {"project": "Atlas", "role": "author", "persona": "dev"})[0] == 200

code, body = post("/ui/project-edit", {"old_name": "Atlas", "name": "Atlas2", "repo": "~/code/atlas"})
assert code == 200 and body["name"] == "Atlas2", body
assert "Atlas" not in names() and "Atlas2" in names(), names()
# ticket label followed the rename
row = conn.execute("SELECT project FROM ticket WHERE title='t'").fetchone()
assert row["project"] == "Atlas2", dict(row)
# staffing key followed the rename
assert "atlas2" in personas.load_staffing() and "atlas" not in personas.load_staffing()
# repo override shows through
proj = next(p for p in get("/ui/projects.json")["projects"] if p["name"] == "Atlas2")
assert proj["repo"], proj  # normalized, non-empty
assert proj["open"] == 1, proj

# renaming onto an existing project name is a 409.
post("/ui/project-create", {"name": "Other", "repo": ""})
assert post("/ui/project-edit", {"old_name": "Atlas2", "name": "Other", "repo": ""})[0] == 409

# --- 3. delete: registry row gone, tickets unfiled (not deleted), staffing cleared.
code, body = post("/ui/project-delete", {"name": "Atlas2"})
assert code == 200 and body["unfiled"] == 1, body
assert "Atlas2" not in names(), names()
row = conn.execute("SELECT project FROM ticket WHERE title='t'").fetchone()
assert row["project"] is None, dict(row)          # unfiled, ticket survives
assert conn.execute("SELECT COUNT(*) c FROM ticket").fetchone()["c"] == 1
assert "atlas2" not in personas.load_staffing()

srv.shutdown()
conn.close()
print("ok: project create/rename/repo-edit/delete, cascade to tickets + staffing")
