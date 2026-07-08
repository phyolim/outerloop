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

"""The 'shipper' role must be recordable in agent_run: on a fresh DB via the schema
CHECK, and on a pre-shipper DB via the one-time table rebuild (the missing role made
every shipper run 500 on INSERT, so opening_pr spun a full agent run per tick until
the stall guard failed the ticket). Plus the two guards that keep a ticket from ever
reaching that dead end again: repo_path canonicalization at intake and the opening_pr
pre-flight fail-fast."""
import subprocess
import types
from outerloop import config, context, db, git_ops
from outerloop.handlers import coding

OLD_AGENT_RUN = """
CREATE TABLE agent_run (
    id            TEXT PRIMARY KEY,
    ticket_id     INTEGER NOT NULL REFERENCES ticket(id),
    role          TEXT NOT NULL
                       CHECK(role IN ('groomer','author','reviewer','fixer','knowledge','ops','triage','scorer')),
    tick_id       TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    worktree_path TEXT,
    exit_code     INTEGER,
    timed_out     INTEGER NOT NULL DEFAULT 0,
    output_json   TEXT,
    cost_usd      REAL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_agent_run_ticket ON agent_run(ticket_id);
"""


def _insert_run(conn, role, id_):
    conn.execute("INSERT INTO agent_run(id, ticket_id, role, tick_id, session_id, prompt)"
                 " VALUES(?, 1, ?, 't', ?, 'p')", (id_, role, id_))


def test_fresh_schema_accepts_shipper():
    conn = db.connect()
    conn.execute("INSERT INTO ticket(id, title, body, type) VALUES(1, 't', '', 'coding')")
    _insert_run(conn, "shipper", "fresh-shipper")
    n = conn.execute("SELECT COUNT(*) c FROM agent_run WHERE role='shipper'").fetchone()["c"]
    assert n == 1
    conn.close()


def test_rebuild_migrates_old_check():
    conn = db.connect()
    # Recreate the pre-shipper table (old CHECK, old column set) with rows in it.
    conn.execute("DROP TABLE agent_run")
    conn.executescript(OLD_AGENT_RUN)
    _insert_run(conn, "author", "old-row")
    try:
        _insert_run(conn, "shipper", "should-fail")
        assert False, "old CHECK unexpectedly accepted 'shipper'"
    except Exception:
        pass
    conn.close()
    conn = db.init_db()  # the migration path every hub start runs
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table'"
                       " AND name='agent_run'").fetchone()["sql"]
    assert "'shipper'" in ddl
    # Old rows survive the rebuild (incl. through the migrated-in token columns)...
    row = conn.execute("SELECT role, tokens_in FROM agent_run WHERE id='old-row'").fetchone()
    assert row["role"] == "author" and row["tokens_in"] == 0
    # ...the rebuild is a no-op on a current DB, and 'shipper' now inserts.
    _insert_run(conn, "shipper", "post-rebuild")
    assert conn.execute("SELECT COUNT(*) c FROM agent_run").fetchone()["c"] == 2
    idx = conn.execute("SELECT 1 FROM sqlite_master WHERE type='index'"
                       " AND name='idx_agent_run_ticket' AND tbl_name='agent_run'").fetchone()
    assert idx, "ticket index missing after rebuild"
    conn.close()


def test_normalize_repo_path():
    # FAKE mode (this harness's default) passes anything through verbatim
    assert git_ops.normalize_repo_path("/tmp/some-repo") == ("/tmp/some-repo", None)
    config.FAKE = False  # real-mode behavior from here down (restored at the end)
    # empty -> stored as NULL, no error
    assert git_ops.normalize_repo_path(None) == (None, None)
    assert git_ops.normalize_repo_path("   ") == (None, None)
    # remote URLs pass through, canonicalized (.git / trailing slash stripped)
    url = "https://github.com/o/r"
    for given in (url, url + ".git", url + "/", "git@github.com:o/r.git"):
        got, err = git_ops.normalize_repo_path(given)
        assert err is None and got.rstrip("/").endswith("o/r"), (given, got)
    # a path that isn't a directory on this machine is rejected with guidance
    got, err = git_ops.normalize_repo_path("/nonexistent/definitely/not/a/repo")
    assert got is None and "use the repo's URL" in err
    try:
        if not shutil.which("git"):
            return  # local-clone cases need a real git binary
        # a local repo WITHOUT origin (the ticket-8 failure shape) is rejected...
        bare = tempfile.mkdtemp(prefix="norm-", dir=_TMP)
        subprocess.run([config.GIT_BIN, "init", "-q", bare], check=True)
        got, err = git_ops.normalize_repo_path(bare)
        assert got is None and "no origin remote" in err
        # ...and WITH origin it resolves to the canonical URL
        subprocess.run([config.GIT_BIN, "-C", bare, "remote", "add", "origin",
                        "https://github.com/o/r.git"], check=True)
        assert git_ops.normalize_repo_path(bare) == ("https://github.com/o/r", None)
    finally:
        config.FAKE = True  # restore the harness default for later tests


def test_opening_pr_fails_fast_on_unresolvable_repo():
    conn = db.connect()
    conn.execute("INSERT INTO ticket(id, title, body, type, status, sub_stage, repo_path,"
                 " handler_state) VALUES(9, 'ship me', '', 'coding', 'active', 'opening_pr',"
                 " '/nonexistent/local/clone', '{\"branch\": \"outerloop/ticket-9-abc\"}')")
    real_cfg = types.SimpleNamespace(FAKE=False, GIT_BIN=config.GIT_BIN, GH_BIN=config.GH_BIN)
    ctx = context.Ctx(conn, real_cfg, "test-tick")
    t = conn.execute("SELECT * FROM ticket WHERE id=9").fetchone()
    out = coding.CodingHandler()._stage_opening_pr(ctx, t, db.hstate(t))
    assert "unshippable" in out, out
    t = conn.execute("SELECT status FROM ticket WHERE id=9").fetchone()
    assert t["status"] == "failed", "deterministic repo error must fail, not retry"
    reason = conn.execute("SELECT reason FROM audit WHERE ticket_id=9 AND action='failed'"
                          " ORDER BY id DESC").fetchone()["reason"]
    assert "cannot ship branch outerloop/ticket-9-abc" in reason
    conn.close()


if __name__ == "__main__":
    test_fresh_schema_accepts_shipper()
    test_rebuild_migrates_old_check()
    test_normalize_repo_path()
    test_opening_pr_fails_fast_on_unresolvable_repo()
    print("PASSED test_shipper_schema")
