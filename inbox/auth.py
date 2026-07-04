"""Per-device bearer-token auth + a bind-address guard. Auth is OFF by default
(loopback dev + tests need none); turn it on for a real LAN/relay deployment with
`python3 -m inbox set-auth on` after provisioning tokens with `set-token`."""

import hashlib
import ipaddress

from . import db


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def resolve(conn, token):
    """Map a bearer token to the device it belongs to, or None."""
    if not token:
        return None
    r = conn.execute("SELECT name FROM device WHERE token_hash=?",
                     (hash_token(token),)).fetchone()
    return r["name"] if r else None


def required(conn):
    return db.get_setting(conn, "require_auth", "off") == "on"


def set_token(conn, device, token):
    th = hash_token(token)
    with db.immediate(conn):
        if conn.execute("SELECT 1 FROM device WHERE name=?", (device,)).fetchone():
            conn.execute("UPDATE device SET token_hash=? WHERE name=?", (th, device))
        else:
            conn.execute("INSERT INTO device(name, token_hash) VALUES(?, ?)", (device, th))


# Ranges we consider "not intentionally public": loopback, the CGNAT block (100.64/10),
# and the private LAN blocks. 0.0.0.0 = all interfaces — LAN-reachable only behind home NAT,
# which is the LAN-only deploy; auth must be on (the LAN hub build sets it). A routable
# public IP is still refused (needs INBOX_ALLOW_PUBLIC_BIND=1).
_SAFE_NETS = [ipaddress.ip_network(c) for c in (
    "100.64.0.0/10", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")]


def is_safe_bind(host):
    if host in ("127.0.0.1", "::1", "localhost", "0.0.0.0"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _SAFE_NETS)
