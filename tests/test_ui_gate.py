"""The dashboard password gate: when settings.ui_token is set, / and /ui/* demand the
secret (ol_ui cookie); empty ui_token stays open. Spins up the real Handler and drives
it over HTTP — no mocking of the request plumbing."""
import os
import sys
import tempfile
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["OUTERLOOP_HOME"] = tempfile.mkdtemp(prefix="inbox-uigate-")

from outerloop import db  # noqa: E402
from outerloop.web import Handler  # noqa: E402

conn = db.init_db()


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _get(port, path, cookie=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        r = urllib.request.urlopen(req)
        return r.status, r.headers.get("Content-Type", ""), r.read().decode(), None
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode(), None


def _post_login(port, secret):
    body = f"secret={secret}".encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/ui/login", data=body, method="POST")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        r = opener.open(req)
        return r.status, r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Set-Cookie")


srv, port = _serve()

# --- gate OFF (no ui_token): /ui/board.json is open and returns JSON ---
status, ctype, _, _ = _get(port, "/ui/board.json")
assert status == 200 and "json" in ctype, f"open board should be JSON, got {status} {ctype}"

# --- gate ON ---
db.set_setting(conn, "ui_token", "hunter2")

status, ctype, body, _ = _get(port, "/ui/board.json")
assert status == 401 and "html" in ctype and "dashboard password" in body, \
    f"gated board should be the 401 login form, got {status} {ctype}"

# wrong password -> no cookie, still the form
code, cookie = _post_login(port, "wrong")
assert code == 200 and not cookie, f"wrong password must not set a cookie, got {code} {cookie}"

# right password -> 303 + Set-Cookie ol_ui=<hash>
code, cookie = _post_login(port, "hunter2")
assert code == 303 and cookie and cookie.startswith("ol_ui="), \
    f"correct password must 303 + set ol_ui cookie, got {code} {cookie}"
ol_ui = cookie.split(";")[0]

# with the cookie -> board is JSON again
status, ctype, _, _ = _get(port, "/ui/board.json", cookie=ol_ui)
assert status == 200 and "json" in ctype, f"authed board should be JSON, got {status} {ctype}"

# a forged/stale cookie is rejected
status, _, body, _ = _get(port, "/ui/board.json", cookie="ol_ui=deadbeef")
assert status == 401 and "dashboard password" in body, "bad cookie must be rejected"

# --- gate OFF again (empty ui_token) -> open, no cookie needed ---
db.set_setting(conn, "ui_token", "")
status, ctype, _, _ = _get(port, "/ui/board.json")
assert status == 200 and "json" in ctype, "clearing ui_token must reopen the board"

srv.shutdown()
print("OK ui_token gate — open by default, password-gates / and /ui/* when set")
print("\n=== UI GATE TEST PASSED ===")
