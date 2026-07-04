# Self-contained: the kind<->type taxonomy, the additive `kind` migration (ALTER ADD
# COLUMN with a CHECK + backfill), and that creation derives type from kind. FAKE.
import os, sys, atexit, shutil, tempfile, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("INBOX_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-taxonomy-")
os.environ["INBOX_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from inbox import taxonomy, db, api

# 1. mapping + fallbacks --------------------------------------------------------
assert taxonomy.type_for("bug") == "coding"
assert taxonomy.type_for("research") == "knowledge"
assert taxonomy.type_for("ops") == "ops"
assert taxonomy.normalize_kind("chore") == "chore"
assert taxonomy.normalize_kind(None, "knowledge") == "research"   # legacy screener path
assert taxonomy.normalize_kind("bogus", None) == "feature"        # invalid -> default
assert set(taxonomy.KINDS) == {"feature", "bug", "chore", "research", "ops"}

# 2. migration mechanism: ALTER ADD COLUMN kind (with CHECK) + NULL passes + backfill.
# Simulate a pre-`kind` ticket table and run the exact statements _migrate uses.
mem = sqlite3.connect(":memory:")
mem.row_factory = sqlite3.Row
mem.execute("CREATE TABLE ticket(id INTEGER PRIMARY KEY, type TEXT NOT NULL)")
for ty in ("coding", "knowledge", "ops"):
    mem.execute("INSERT INTO ticket(type) VALUES(?)", (ty,))
db._ensure_columns(mem, "ticket",
                   {"kind": "kind TEXT CHECK(kind IN ('feature','bug','chore','research','ops'))"})
# Existing rows are NULL kind — NULL must pass the CHECK (unknown != false), then backfill.
mem.execute("UPDATE ticket SET kind = CASE type WHEN 'coding' THEN 'feature'"
            " WHEN 'knowledge' THEN 'research' WHEN 'ops' THEN 'ops' ELSE 'feature' END"
            " WHERE kind IS NULL")
got = {r["type"]: r["kind"] for r in mem.execute("SELECT type, kind FROM ticket")}
assert got == {"coding": "feature", "knowledge": "research", "ops": "ops"}, got
# CHECK actually enforces the domain on new writes.
mem.execute("INSERT INTO ticket(type, kind) VALUES('coding','bug')")
try:
    mem.execute("INSERT INTO ticket(type, kind) VALUES('coding','nope')")
    raise AssertionError("CHECK should reject an unknown kind")
except sqlite3.IntegrityError:
    pass
# _ensure_columns is idempotent (column already present -> no-op, no raise).
db._ensure_columns(mem, "ticket",
                   {"kind": "kind TEXT CHECK(kind IN ('feature','bug','chore','research','ops'))"})

# 3. real DB: init applies the fresh-schema kind CHECK; create derives type from kind.
conn = db.init_db()
r1 = api._create_ticket(conn, {"title": "a bug", "kind": "bug"})[1]
row = conn.execute("SELECT type, kind FROM ticket WHERE id=?", (r1["id"],)).fetchone()
assert (row["type"], row["kind"]) == ("coding", "bug"), tuple(row)
# legacy caller sets only `type` (screener) -> kind derived, still valid.
r2 = api._create_ticket(conn, {"title": "legacy", "type": "knowledge"})[1]
row = conn.execute("SELECT type, kind FROM ticket WHERE id=?", (r2["id"],)).fetchone()
assert (row["type"], row["kind"]) == ("knowledge", "research"), tuple(row)

# 4. the per-kind hint reaches the coding groomer prompt (feature vs bug differ).
from inbox.tick import run_tick
bug = api._create_ticket(conn, {"title": "crash on save", "kind": "bug", "repo_path": "/x"})[1]["id"]
feat = api._create_ticket(conn, {"title": "add export", "kind": "feature", "repo_path": "/x"})[1]["id"]
for _ in range(8):   # triage -> score -> seed(groom) for both tickets
    run_tick()
    c2 = db.connect()
    have = {r["ticket_id"] for r in c2.execute(
        "SELECT DISTINCT ticket_id FROM agent_run WHERE role='groomer'")}
    c2.close()
    if {bug, feat} <= have:
        break
c2 = db.connect()
def groom_prompt(tid):
    return c2.execute("SELECT prompt FROM agent_run WHERE ticket_id=? AND role='groomer'"
                      " ORDER BY rowid DESC LIMIT 1", (tid,)).fetchone()["prompt"]
assert "bug fix" in groom_prompt(bug), groom_prompt(bug)
assert "new feature" in groom_prompt(feat), groom_prompt(feat)
c2.close()
print("OK taxonomy: mapping, migration, CHECK, create-derives-type, per-kind prompt hint")
