# Self-contained: worker self-update over loopback, FAKE mode. No deps.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-update-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
from outerloop import db as _bdb
_bdb.init_db()
# --- test body ---
import io, tarfile, threading, time
from http.server import ThreadingHTTPServer
import outerloop
from outerloop import db, client
from outerloop import worker as W
from outerloop.hub import HubHandler

PORT = 8801
BASE = f"http://127.0.0.1:{PORT}"

srv = ThreadingHTTPServer(("127.0.0.1", PORT), HubHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)

print("=== worker self-update (hub + worker over loopback, FAKE) ===")

# 1. Heartbeat advertises the hub's version.
hb = client.post(BASE, "/api/heartbeat", {"worker": "pro", "capabilities": [], "version": "old"})
assert "hub_version" in hb, "heartbeat missing hub_version"
assert hb["hub_version"] == outerloop.__version__, f"hub_version {hb['hub_version']} != {outerloop.__version__}"
print(f"OK heartbeat advertises hub_version={hb['hub_version']}")

# 2. GET /api/update returns a valid tar.gz with the right membership (auth off).
data = client.download(BASE, "/api/update")
tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
names = tf.getnames()
tf.close()
assert "outerloop/__init__.py" in names, "tarball missing outerloop/__init__.py"
assert "deploy.env" not in names, "tarball leaked deploy.env"
assert "settings.json" not in names, "tarball leaked settings.json"
assert not any("data" in n.split("/") for n in names), "tarball leaked a data/ path"
print(f"OK /api/update tar.gz: {len(names)} members, has outerloop/__init__.py, no deploy.env/settings.json/data")

# 3. Dogfood the worker extractor into a fresh temp dir (NEVER the real tree).
ext = tempfile.mkdtemp(prefix="inbox-extract-")
try:
    W._extract_update(data, ext)
    assert os.path.exists(os.path.join(ext, "outerloop", "__init__.py")), "extract did not lay down outerloop/__init__.py"
    print("OK worker extractor lays down outerloop/__init__.py under a temp dir")
finally:
    shutil.rmtree(ext, ignore_errors=True)

# 4. Auth: with require_auth on, an unauthenticated GET /api/update gets 401.
c = db.connect()
db.set_setting(c, "require_auth", "on")
c.close()
try:
    client.download(BASE, "/api/update")
    raise SystemExit("FAIL: unauthenticated /api/update was accepted with require_auth on")
except client.APIError as e:
    assert e.code == 401, f"expected 401, got {e.code}"
finally:
    c = db.connect()
    db.set_setting(c, "require_auth", "off")
    c.close()
print("OK auth: unauthenticated /api/update gets 401 when require_auth is on")

# 5. Path-traversal guard: a member named "../evil.txt" must be rejected, nothing written.
guard_dir = tempfile.mkdtemp(prefix="inbox-guard-")
outside = os.path.join(os.path.dirname(guard_dir), "evil.txt")
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as mtf:
    payload = b"pwned"
    ti = tarfile.TarInfo(name="../evil.txt")
    ti.size = len(payload)
    mtf.addfile(ti, io.BytesIO(payload))
evil = buf.getvalue()
try:
    W._extract_update(evil, guard_dir)
    raise SystemExit("FAIL: path-traversal tarball was extracted")
except ValueError:
    pass
finally:
    assert not os.path.exists(outside), f"path-traversal wrote outside the temp dir: {outside}"
    shutil.rmtree(guard_dir, ignore_errors=True)
print("OK path-traversal: '../evil.txt' member rejected, nothing written outside the temp dir")

# 6. Version gate. Same version -> no-op (safe to call directly).
assert W._maybe_self_update(BASE, None, outerloop.__version__, "pro") is False, "same version must be a no-op"
# Mismatch path -> True, WITHOUT touching the live tree: stub download + extract.
_orig_extract, _orig_download = W._extract_update, client.download
try:
    W._extract_update = lambda data, root: None
    client.download = lambda *a, **k: b"x"
    assert W._maybe_self_update(BASE, None, "999.0.0", "pro") is True, "version mismatch must return True"
finally:
    W._extract_update = _orig_extract
    client.download = _orig_download
print("OK version gate: same-version no-op; mismatch returns True (caller then exits non-zero)")

print("\n=== WORKER SELF-UPDATE TESTS PASSED ===")
srv.shutdown()
