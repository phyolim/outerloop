"""Tiny stdlib HTTP client the worker uses to talk to the hub. No deps."""

import json
import urllib.error
import urllib.request


class APIError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"HTTP {code}: {msg}")
        self.code = code


def request(method, base_url, path, body=None, token=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            msg = json.loads(raw).get("error", "")
        except Exception:
            msg = raw.decode(errors="replace")
        raise APIError(e.code, msg)


def download(base_url, path, token=None, timeout=120):
    """GET returning the RAW response bytes (no json parsing) — for binary payloads
    like the update tarball. Same auth/error handling as request()."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base_url.rstrip("/") + path, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            msg = json.loads(raw).get("error", "")
        except Exception:
            msg = raw.decode(errors="replace")
        raise APIError(e.code, msg)


def post(base_url, path, body, token=None, timeout=120):
    status, obj = request("POST", base_url, path, body, token, timeout)
    return obj


def get(base_url, path, token=None, timeout=120):
    status, obj = request("GET", base_url, path, None, token, timeout)
    return obj
