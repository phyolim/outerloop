# External intake: POST /api/intake turns a webhook/curl payload into a ticket, gated
# by its own shared secret (settings.intake_token) — NOT a device bearer token, and it
# must keep working when require_auth=on (the vacation story: Sentry has no device).
# Runs against the real coordinator handler. FAKE mode, throwaway DB.
import os, sys, atexit, shutil, tempfile, threading, time, json
from http.server import ThreadingHTTPServer
from urllib.request import urlopen, Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("INBOX_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-intake-")
os.environ["INBOX_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

from inbox import db
from inbox.coordinator import CoordHandler

db.init_db()
c = db.connect()

PORT = 8824
BASE = f"http://127.0.0.1:{PORT}"
srv = ThreadingHTTPServer(("127.0.0.1", PORT), CoordHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)


def post(path, obj, bearer=None):
    h = {"Content-Type": "application/json"}
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    req = Request(BASE + path, data=json.dumps(obj).encode(), headers=h, method="POST")
    try:
        r = urlopen(req)
        return r.status, json.loads(r.read())
    except Exception as e:
        return e.code, json.loads(e.read())


# 1. Default-deny: no intake_token configured -> endpoint is off.
st, r = post("/api/intake", {"title": "x"})
assert st == 403 and "disabled" in r["error"], r

db.set_setting(c, "intake_token", "s3cret")

# 2. Wrong/missing token -> 403; nothing created.
st, _ = post("/api/intake", {"title": "x"})
assert st == 403
st, _ = post("/api/intake?token=wrong", {"title": "x"})
assert st == 403
assert c.execute("SELECT COUNT(*) n FROM ticket").fetchone()["n"] == 0

# 3. Generic payload via ?token= (header-less webhook senders).
st, r = post("/api/intake?token=s3cret",
             {"title": "user reports crash on login", "kind": "bug", "project": "myapp"})
assert st == 200, r
t = c.execute("SELECT * FROM ticket WHERE id=?", (r["id"],)).fetchone()
assert t["kind"] == "bug" and t["type"] == "coding" and t["project"] == "myapp"
assert t["status"] == "inbox" and t["draft"] == 0, "intake enters the pipeline (not a draft)"
a = c.execute("SELECT actor FROM audit WHERE ticket_id=?", (t["id"],)).fetchone()
assert a["actor"] == "intake"

# 4. Same secret as Bearer works too.
st, r = post("/api/intake", {"title": "bearer works"}, bearer="s3cret")
assert st == 200

# 5. Sentry issue-alert shape: title/permalink extracted, filed as a bug, and the issue
#    id becomes the dedup_key so alert re-fires don't flood the queue.
sentry = {"action": "created",
          "data": {"issue": {"id": "42", "title": "TypeError in checkout",
                             "culprit": "app/cart.py in total",
                             "permalink": "https://sentry.example/issues/42/"}}}
st, r = post("/api/intake?token=s3cret", sentry)
assert st == 200, r
t = c.execute("SELECT * FROM ticket WHERE id=?", (r["id"],)).fetchone()
assert t["title"] == "TypeError in checkout" and t["kind"] == "bug"
assert "sentry.example" in t["body"] and t["dedup_key"] == "sentry-42"
st, r2 = post("/api/intake?token=s3cret", sentry)
assert r2.get("dedup") is True and r2["id"] == r["id"], "re-fired alert must dedup"

# 6. Junk payload with no title -> 400.
st, _ = post("/api/intake?token=s3cret", {"foo": "bar"})
assert st == 400

# 7. THE regression that matters: require_auth=on must not lock intake out (Sentry
#    can't hold a device token), while the rest of /api/* still 401s without one.
db.set_setting(c, "require_auth", "on")
st, _ = post("/api/tickets", {"title": "no token"})
assert st == 401, "device API must still require auth"
st, r = post("/api/intake?token=s3cret", {"title": "works while auth on"})
assert st == 200, r

srv.shutdown()
print("ok: intake default-deny, secret-gated, sentry-mapped, deduped, auth-exempt")
