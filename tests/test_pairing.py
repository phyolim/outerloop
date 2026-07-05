# Self-contained: the LAN pairing flow. A worker requests with a code-check hash
# (the code itself never transmitted), the hub confirms the typed code, mints the
# token, and the worker decrypts it with the code. Guardrails: LAN-only (loopback
# = relay path refused), wrong-code attempt cap, TTL expiry, single-use delivery,
# per-IP throttle. FAKE mode, throwaway DB, no deps.
import atexit
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="pairing-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

import json
import secrets

from outerloop import auth, config, db, pairing

db.init_db()
conn = db.connect()

LAN_IP = "192.168.1.24"


def request(name="mbp", ip=LAN_IP, code=None, salt=None):
    code = code or pairing.make_code()
    salt = salt or secrets.token_bytes(32)
    st, obj = pairing.handle_api("POST", "/api/pair/request",
                                 {"name": name, "host_info": "macOS 15.3 · arm64",
                                  "salt": salt.hex(),
                                  "code_check": pairing.code_check(code, salt)},
                                 conn, ip)
    return st, obj, code, salt


# --- happy path: request → confirm → single-use encrypted delivery ---
pairing.reset()
st, obj, code, salt = request()
assert st == 200 and obj["request_id"], obj
rid = obj["request_id"]

# hub sees it pending
pend = pairing.pending()
assert len(pend) == 1 and pend[0]["name"] == "mbp" and pend[0]["ip"] == LAN_IP

# worker poll: still pending
st, s = pairing.handle_api("GET", f"/api/pair/status/{rid}", {}, conn, LAN_IP)
assert st == 200 and s["state"] == "pending"

# human types the code (dash + lowercase tolerated)
ok, err = pairing.confirm(conn, rid, code[:3].lower() + "-" + code[3:])
assert ok, err

# worker poll: confirmed, decrypts the real minted token
st, s = pairing.handle_api("GET", f"/api/pair/status/{rid}", {}, conn, LAN_IP)
assert st == 200 and s["state"] == "confirmed" and s["worker"] == "mbp"
token = pairing.decrypt_token(code, salt, s["token_enc"], s["mac"])
assert token and auth.resolve(conn, token) == "mbp", "decrypted token must be the minted one"
# tampered MAC -> refused
assert pairing.decrypt_token(code, salt, s["token_enc"], "0" * 64) is None

# single-use: a second poll gets nothing
st, s = pairing.handle_api("GET", f"/api/pair/status/{rid}", {}, conn, LAN_IP)
assert s["state"] == "expired"

# pairing seeded the default caps (so the worker can claim work immediately)…
row = conn.execute("SELECT capabilities FROM worker WHERE name='mbp'").fetchone()
assert json.loads(row["capabilities"]) == config.DEFAULT_CAPS, row["capabilities"]
# …but re-pairing must NOT clobber a curated set
conn.execute("UPDATE worker SET capabilities='[\"market-data\"]' WHERE name='mbp'")
conn.commit()
pairing.reset()
st, obj, code, salt = request()
ok, err = pairing.confirm(conn, obj["request_id"], code)
assert ok, err
row = conn.execute("SELECT capabilities FROM worker WHERE name='mbp'").fetchone()
assert json.loads(row["capabilities"]) == ["market-data"], row["capabilities"]

# --- LAN-only: loopback (the relay path) is refused ---
pairing.reset()
st, obj, *_ = request(ip="127.0.0.1")
assert st == 403, obj
st, obj = pairing.handle_api("GET", "/api/pair/info", {}, conn, "127.0.0.1")
assert st == 403
st, obj = pairing.handle_api("GET", "/api/pair/info", {}, conn, LAN_IP)
assert st == 200 and "version" in obj and "workers" in obj

# --- wrong code: capped attempts, then the request is dropped ---
pairing.reset()
st, obj, code, salt = request()
rid = obj["request_id"]
for i in range(pairing.MAX_ATTEMPTS):
    ok, err = pairing.confirm(conn, rid, "AAAAAA")
    assert not ok
ok, err = pairing.confirm(conn, rid, code)   # even the right code is now too late
assert not ok and "expired" in err, err

# --- per-IP throttle: back-to-back requests are refused ---
pairing.reset()
st, *_ = request()
assert st == 200
st, obj, *_ = request()
assert st == 429, obj

# --- TTL expiry ---
pairing.reset()
st, obj, code, salt = request()
rid = obj["request_id"]
with pairing._lock:
    pairing._requests[rid]["at"] -= pairing.TTL_SEC + 1
assert pairing.pending() == []
ok, err = pairing.confirm(conn, rid, code)
assert not ok
st, s = pairing.handle_api("GET", f"/api/pair/status/{rid}", {}, conn, LAN_IP)
assert s["state"] == "expired"

# --- bad payloads ---
pairing.reset()
st, obj = pairing.handle_api("POST", "/api/pair/request",
                             {"name": "", "salt": "zz", "code_check": ""}, conn, LAN_IP)
assert st == 400

conn.close()
print("PASSED: pairing request/confirm/decrypt roundtrip + LAN-only, attempt cap, TTL, throttle")
