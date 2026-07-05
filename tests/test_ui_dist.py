# Proves the brew-shipped React board mechanism: when ui/dist exists, web.py serves
# the built SPA's index.html at / AND at every app route (history-API fallback, so
# /fleet etc. deep-link); when it's absent, an honest 'UI not built' page — the JSON
# seam works either way. Self-contained FAKE-mode, throwaway DB + temp UI_DIST.
import os, sys, atexit, shutil, tempfile, threading, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-uidist-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
from outerloop import db, config, web
from http.server import ThreadingHTTPServer

db.init_db()

# Stand in for a real `npm run build` output: the one file web.py serves at /.
dist = os.path.join(_TMP, "ui", "dist")
os.makedirs(dist)
STUB = "<!doctype html><title>REACT_SPA_STUB</title>"
with open(os.path.join(dist, "index.html"), "w") as f:
    f.write(STUB)

srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/"


def get(path="/"):
    return urllib.request.urlopen(url.rstrip("/") + path).read().decode()


# ui/dist present -> the built SPA is served verbatim at /.
config.UI_DIST = __import__("pathlib").Path(dist)
assert "REACT_SPA_STUB" in get("/"), "web.py must serve ui/dist/index.html when present"

# History-API fallback: the app's routes (/fleet, /parked, /log, /decisions, /ticket/N)
# deep-link straight into the SPA — index.html, not a 404 and not a legacy page.
for p in ("/fleet", "/parked", "/log", "/decisions", "/done", "/insights", "/ticket/7"):
    assert "REACT_SPA_STUB" in get(p), f"{p} must fall back to the SPA's index.html"

# ...but the JSON seam is never swallowed by the fallback.
import json as _json
board = _json.loads(get("/ui/board.json"))
assert "columns" in board, "/ui/*.json must keep serving JSON, not index.html"

# ui/dist absent -> an honest 'UI not built' page (the server-rendered UI is gone),
# while the JSON API keeps working for curl/verification.
config.UI_DIST = __import__("pathlib").Path(os.path.join(_TMP, "does", "not", "exist"))
body = get("/")
assert "REACT_SPA_STUB" not in body, "must not serve the stub once ui/dist is gone"
assert "UI not built" in body, "absent dist must say the UI is missing, not pretend"
assert "columns" in _json.loads(get("/ui/board.json")), "JSON API must survive a missing dist"

srv.shutdown()
print("OK ui/dist served at / and all app routes; JSON seam intact; honest page when absent")
print("\n=== UI_DIST TEST PASSED ===")
