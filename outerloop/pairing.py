"""LAN worker pairing.

A worker initiates with POST /api/pair/request and displays a 6-char code on ITS
screen; the human types that code on the hub, which mints the bearer token and
hands it back encrypted to a key derived from the code + the request's one-time
salt. The code itself never crosses the wire — typing it on the hub is the proof
of physical control. Guardrails: 2-minute TTL, single-use delivery, 5 wrong-code
attempts, per-IP throttling, LAN-only (the relay path arrives via loopback and is
refused; remote workers keep the manual name+token flow).

ponytail: in-memory store — pairing is ephemeral (2-min TTL) and the hub is one
process, so nothing needs to survive a restart. Move to a table if the hub ever
goes multi-process.
"""

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time

from . import __version__, auth, config, db

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 32 chars, no 0/O/1/I ≈ 10⁹ codes
CODE_LEN = 6
TTL_SEC = 120
MAX_ATTEMPTS = 5
MAX_PENDING_PER_IP = 3
MIN_REQUEST_GAP_SEC = 5

_lock = threading.Lock()
_requests = {}          # request_id -> state dict
_last_request_at = {}   # ip -> time.monotonic() of its last accepted request


def make_code():
    """Worker-side helper (also used by tests): a display code like 7KF-P2M."""
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def code_check(code, salt):
    """What the worker sends instead of the code: H(salt ‖ code)."""
    return hashlib.sha256(salt + code.encode()).hexdigest()


def _derive_key(code, salt):
    # PBKDF2 so a passive observer who captured salt+ciphertext still faces
    # ~10⁹ × 100k-iteration work to brute the code space offline.
    return hashlib.pbkdf2_hmac("sha256", code.encode(), salt, 100_000)


def _xor_stream(key, n):
    out = b""
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(key + counter.to_bytes(4, "big")).digest()
        counter += 1
    return out[:n]


def encrypt_token(code, salt, token):
    """(cipher_hex, mac_hex) — stdlib-only stream cipher + MAC, keyed off the code."""
    k = _derive_key(code, salt)
    tb = token.encode()
    enc = bytes(a ^ b for a, b in zip(tb, _xor_stream(k, len(tb))))
    return enc.hex(), hmac.new(k, enc, hashlib.sha256).hexdigest()


def decrypt_token(code, salt, cipher_hex, mac_hex):
    """The worker's side of encrypt_token. Returns the token, or None on a bad MAC."""
    k = _derive_key(code, salt)
    enc = bytes.fromhex(cipher_hex)
    if not hmac.compare_digest(hmac.new(k, enc, hashlib.sha256).hexdigest(), mac_hex):
        return None
    return bytes(a ^ b for a, b in zip(enc, _xor_stream(k, len(enc)))).decode()


def _lan_only(ip):
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    # loopback = the relay tunnel's local end → refused by design; remote workers
    # pair manually with a token.
    return a.is_private and not a.is_loopback


def _prune(now=None):
    now = now or time.time()
    for rid in [rid for rid, r in _requests.items() if now - r["at"] > TTL_SEC]:
        del _requests[rid]
    # The throttle map only matters within MIN_REQUEST_GAP_SEC; drop stale entries
    # (keyed by IP, monotonic clock) so it can't grow unbounded over the hub's uptime.
    mono = time.monotonic()
    for ip in [ip for ip, t in _last_request_at.items() if mono - t > TTL_SEC]:
        del _last_request_at[ip]


def _expires_in(r):
    return max(0, int(TTL_SEC - (time.time() - r["at"])))


# --- the un-authenticated /api/pair/* seam (LAN-only; wired pre-auth in hub.py) ---
def handle_api(method, path, body, conn, client_ip):
    if method == "GET" and path == "/api/pair/info":
        # discovery detail line ("hub.local :8765 · v0.2.1 · 4 workers") — harmless
        # metadata, but still LAN-only like everything else on this seam.
        if not _lan_only(client_ip):
            return 403, {"error": "pairing is LAN-only"}
        n = conn.execute("SELECT COUNT(*) c FROM worker").fetchone()["c"]
        return 200, {"name": socket.gethostname().split(".")[0],
                     "version": __version__, "workers": n}
    if not _lan_only(client_ip):
        return 403, {"error": "pairing is LAN-only"}

    if method == "POST" and path == "/api/pair/request":
        name = (body.get("name") or "").strip()[:32]
        salt = body.get("salt") or ""
        check = body.get("code_check") or ""
        if not name or len(salt) != 64 or len(check) != 64:
            return 400, {"error": "name, salt (32B hex), code_check required"}
        try:
            bytes.fromhex(salt)
        except ValueError:
            return 400, {"error": "salt must be hex"}
        with _lock:
            _prune()
            now = time.monotonic()
            if now - _last_request_at.get(client_ip, -MIN_REQUEST_GAP_SEC) < MIN_REQUEST_GAP_SEC:
                return 429, {"error": "slow down"}
            if sum(1 for r in _requests.values() if r["ip"] == client_ip) >= MAX_PENDING_PER_IP:
                return 429, {"error": "too many pending requests from this address"}
            _last_request_at[client_ip] = now
            rid = secrets.token_hex(16)
            _requests[rid] = {
                "id": rid, "name": name, "ip": client_ip,
                "host_info": str(body.get("host_info") or "")[:120],
                "salt": salt, "code_check": check,
                "at": time.time(), "attempts": 0, "state": "pending",
                "token_enc": None, "mac": None,
            }
        db.append_audit(conn, "fleet", "pair_requested",
                        f"{name} ({client_ip}) asked to join the fleet")
        return 200, {"request_id": rid, "expires_in": TTL_SEC}

    if method == "GET" and path.startswith("/api/pair/status/"):
        rid = path.rsplit("/", 1)[-1]
        with _lock:
            _prune()
            r = _requests.get(rid)
            if not r:
                return 200, {"state": "expired"}
            if r["state"] == "confirmed":
                del _requests[rid]   # single-use delivery
                return 200, {"state": "confirmed", "worker": r["name"],
                             "token_enc": r["token_enc"], "mac": r["mac"]}
            return 200, {"state": "pending", "expires_in": _expires_in(r)}

    return 404, {"error": "not found"}


# --- the hub-side seam (cookie-gated /ui/* in web.py) ------------------------
def pending():
    with _lock:
        _prune()
        return [{"request_id": r["id"], "name": r["name"], "host_info": r["host_info"],
                 "ip": r["ip"], "expires_in": _expires_in(r),
                 "attempts_left": MAX_ATTEMPTS - r["attempts"]}
                for r in sorted(_requests.values(), key=lambda r: r["at"])
                if r["state"] == "pending"]


def confirm(conn, rid, code):
    """Verify the typed code; on match mint the bearer token and stage it for the
    worker's next status poll. Returns (ok, error_message)."""
    code = (code or "").strip().upper().replace("-", "")
    if len(code) != CODE_LEN:
        return False, "code is 6 characters"
    with _lock:
        _prune()
        r = _requests.get(rid)
        if not r or r["state"] != "pending":
            return False, "request expired — ask the worker to try again"
        salt = bytes.fromhex(r["salt"])
        if not hmac.compare_digest(code_check(code, salt), r["code_check"]):
            r["attempts"] += 1
            if r["attempts"] >= MAX_ATTEMPTS:
                del _requests[rid]
                return False, "too many wrong codes — request dropped"
            return False, f"wrong code — {MAX_ATTEMPTS - r['attempts']} tries left"
        token = secrets.token_hex(24)
        auth.set_token(conn, r["name"], token)
        # Seed the broad default caps so the new worker is immediately eligible for
        # work — but only if it has none (re-pairing must not clobber a curated set).
        conn.execute("UPDATE worker SET capabilities=? WHERE name=?"
                     " AND capabilities IN ('[]', '')",
                     (json.dumps(config.DEFAULT_CAPS), r["name"]))
        r["token_enc"], r["mac"] = encrypt_token(code, salt, token)
        r["state"] = "confirmed"
        name = r["name"]
    db.append_audit(conn, "human", "worker_paired",
                    f"{name}: paired via code confirmation, caps seeded")
    return True, None


def ignore(rid):
    with _lock:
        _requests.pop(rid, None)


def reset():
    """Test hook: drop all pairing state."""
    with _lock:
        _requests.clear()
        _last_request_at.clear()
