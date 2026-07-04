"""A worker daemon. One per Mac (launchd KeepAlive). It pulls work from the hub and
runs exactly one bounded stage locally through the SAME handlers the single-box loop
uses — only now ctx is a RemoteCtx, so every write is an epoch-fenced POST to the hub.

Config via env: INBOX_DEVICE (name), INBOX_CAPABILITIES (JSON array),
INBOX_HUB (base url), INBOX_DEVICE_TOKEN (bearer; optional until stage 8)."""

import io
import json
import os
import tarfile
import time
import uuid

from . import __version__, client, config
from .context import RemoteCtx, StaleEpoch
from .handlers import get_handler


def _run_one(base, token, device, t, epoch):
    ctx = RemoteCtx(base, token, t["id"], epoch, tick_id=f"{device}-{uuid.uuid4().hex[:6]}")
    handler = get_handler(t["type"])
    outcome = "advanced"
    try:
        handler.advance(ctx, t)
    except StaleEpoch:
        outcome = "stale (lease reclaimed mid-stage)"
    except Exception as e:  # noqa: BLE001 — one ticket must not kill the worker
        outcome = f"error: {e}"
        try:
            ctx.write("append_audit", actor="worker", action="error", reason=str(e)[:200])
        except Exception:
            pass
    finally:
        try:
            client.post(base, "/api/release", {"ticket_id": t["id"], "epoch": epoch}, token=token)
        except Exception:
            pass
    print(f"[{device}] ticket {t['id']} ({t['type']}/{t.get('sub_stage')}): {outcome}")


def _extract_update(data, dest_root):
    """Extract a gzip tarball (bytes) over dest_root. Validates it opens as a readable
    tar.gz BEFORE touching the live tree (a bad download raises here, caller treats it
    as failure). Rejects members whose resolved path escapes dest_root.
    # ponytail: plain extract-over-top — a mid-extract crash can leave a half-written
    # tree. Upgrade path: extract to a versioned dir + symlink flip."""
    dest_root = os.path.abspath(dest_root)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            # Resolve where this member would land; reject absolute paths / .. escapes.
            target = os.path.abspath(os.path.join(dest_root, m.name))
            if target != dest_root and not target.startswith(dest_root + os.sep):
                raise ValueError(f"unsafe path in update tarball: {m.name}")
        tf.extractall(dest_root, members=members)


def _maybe_self_update(base, token, hub_version, device):
    """If hub advertises a different version, download /api/update and extract it over
    the app root. Returns True on a successful update (caller must then exit non-zero
    so launchd restarts on the new code). Any failure logs and returns False — an
    update must never crash the worker or skip normal work (poll interval is backoff)."""
    if not hub_version or hub_version == __version__:
        return False
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        print(f"[{device}] updating {__version__} -> {hub_version}")
        data = client.download(base, "/api/update", token=token)
        _extract_update(data, app_root)
        return True
    except Exception as e:  # noqa: BLE001 — an update failure must not kill the worker
        print(f"[{device}] self-update failed: {e}")
        return False


def run_worker():
    device = os.environ.get("INBOX_DEVICE", "worker")
    caps = json.loads(os.environ.get("INBOX_CAPABILITIES", "[]"))
    base = config.local_setting("hub_url") or os.environ.get("INBOX_HUB")
    if not base:
        # Fresh worker with no hub configured: don't spin against loopback. Exit cleanly
        # so launchd (KeepAlive = SuccessfulExit:false) leaves us stopped until the hub
        # URL is set in the menu-bar Settings, which kickstarts us back to life.
        print(f"worker '{device}': no hub URL set — configure it in the menu-bar Settings. Idle.")
        return
    token = os.environ.get("INBOX_DEVICE_TOKEN")
    poll = config.WORKER_POLL_SEC
    print(f"worker '{device}' caps={caps} -> {base} (FAKE={config.FAKE})")
    while True:
        try:
            hb = client.post(base, "/api/heartbeat",
                             {"device": device, "capabilities": caps, "version": __version__},
                             token=token)
            fake_before = config.FAKE
            config.apply_hub_cfg(hb.get("cfg"))  # fleet behavior is hub-owned
            if config.FAKE != fake_before:
                print(f"[{device}] inherited hub cfg: FAKE={config.FAKE}")
            if _maybe_self_update(base, token, hb.get("hub_version"), device):
                # launchd plist KeepAlive={SuccessfulExit:false}: exit 0 would leave us
                # STOPPED; non-zero makes launchd restart us on the freshly-extracted code.
                raise SystemExit(1)
            if hb.get("status") not in (None, "online"):   # paused/draining => idle
                time.sleep(poll)
                continue
            claimed = client.post(base, "/api/claim", {"device": device}, token=token)
            t = claimed.get("ticket") if claimed else None
            if not t:
                time.sleep(poll)
                continue
            _run_one(base, token, device, t, claimed["epoch"])
        except client.APIError as e:
            print(f"[{device}] hub error {e.code}; backing off")
            time.sleep(poll * 2)
        except OSError as e:  # hub unreachable — idle and back off, never act alone
            print(f"[{device}] hub unreachable ({e}); backing off")
            time.sleep(poll * 2)


def run_worker_once():
    """One heartbeat+claim+stage+release cycle. Returns True if a ticket was worked.
    Used by the FAKE multi-node test to drive workers deterministically."""
    device = os.environ.get("INBOX_DEVICE", "worker")
    caps = json.loads(os.environ.get("INBOX_CAPABILITIES", "[]"))
    base = config.local_setting("hub_url") or os.environ.get("INBOX_HUB") or "http://127.0.0.1:8765"
    token = os.environ.get("INBOX_DEVICE_TOKEN")
    hb = client.post(base, "/api/heartbeat", {"device": device, "capabilities": caps,
                                              "version": __version__}, token=token)
    config.apply_hub_cfg(hb.get("cfg"))
    claimed = client.post(base, "/api/claim", {"device": device}, token=token)
    t = claimed.get("ticket") if claimed else None
    if not t:
        return False
    _run_one(base, token, device, t, claimed["epoch"])
    return True
