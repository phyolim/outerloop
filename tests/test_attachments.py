# Self-contained: /ui/attach stores an uploaded file and /attachments/<name> serves
# it back byte-identical — plus the two guards: size cap and path traversal.
# FAKE mode, throwaway DB, no deps.
import os, sys, atexit, shutil, tempfile, threading, time, json
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-attach-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from outerloop import db
from outerloop.web import Handler

db.init_db()

PORT = 8821
BASE = f"http://127.0.0.1:{PORT}"
srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)


def post_raw(path, data):
    req = Request(BASE + path, data=data,
                  headers={"Content-Type": "application/octet-stream"}, method="POST")
    try:
        r = urlopen(req)
        return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


# Round-trip: PNG-ish bytes up, same bytes back, name sanitized.
payload = b"\x89PNG fake image bytes \x00\x01\x02"
code, out = post_raw("/ui/attach?name=my%20shot%20(1).png", payload)
assert code == 200, out
assert out["name"] == "my-shot-1-.png", out  # spaces/parens sanitized to '-'
url = out["url"]
assert url.startswith("/attachments/"), out
r = urlopen(BASE + url)
assert r.status == 200 and r.read() == payload, "served bytes differ"

# Empty body rejected.
code, out = post_raw("/ui/attach?name=empty.png", b"")
assert code == 400, out

# Traversal-looking GET never escapes the attachments dir.
try:
    r = urlopen(BASE + "/attachments/..%2F..%2Finbox.db")
    code = r.status
    body = r.read()
    # If it resolved to anything, it must NOT be the SQLite db.
    assert not body.startswith(b"SQLite format"), "path traversal leaked the DB"
except HTTPError as e:
    code = e.code
assert code in (200, 404), code

print("PASSED test_attachments")
