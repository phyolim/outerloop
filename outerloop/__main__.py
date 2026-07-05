"""CLI: python3 -m outerloop {init|serve|tick}"""

import sys

from . import config, db


def _arg(i, prompt):
    """Positional arg i, or prompt for it interactively when it's missing. On EOF
    (no input available — e.g. a closed stdin) exit with a clean message, not a
    traceback."""
    if len(sys.argv) > i:
        return sys.argv[i]
    try:
        return input(prompt).strip()
    except EOFError:
        sys.exit(f"missing argument: {prompt.rstrip(': ')}")


def _setup_hub(secret, role="hub"):
    """Configure this box as a real LAN hub with auth on — the intended hub default.
    Binding 0.0.0.0 is a trust boundary, so a dashboard password (ui_token) is set too:
    `auth on` (require_auth) only guards the worker API, not the web board. role="both"
    is the same hardened hub, but `service` also runs a co-located worker on it."""
    config.set_local("role", role)
    config.set_local("bind", "0.0.0.0")
    config.set_local("fake", "0")
    conn = db.init_db()
    db.set_setting(conn, "require_auth", "on")
    db.set_setting(conn, "ui_token", secret)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd in ("version", "--version", "-v"):
        from . import __version__
        print(__version__)
    elif cmd == "init":
        db.init_db()
        print(f"initialized {config.DB_PATH}  (FAKE mode: {config.FAKE})")
    elif cmd == "serve":
        from .web import serve
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
        serve(port)
    elif cmd == "tick":
        from .tick import run_tick
        run_tick()
    elif cmd == "hub":
        from .hub import run_hub
        host = sys.argv[2] if len(sys.argv) > 2 else None
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 8765
        run_hub(host, port)
    elif cmd == "worker":
        from .worker import run_worker
        run_worker()
    elif cmd == "service":
        # One entry point for launchd/`brew services`: run whatever role this box is.
        role = config.local_setting("role")
        if role == "worker":
            from .worker import run_worker
            run_worker()
        elif role in ("both", "hub+worker"):
            # This box is the hub AND a worker: one process, hub in the main thread +
            # a co-located worker thread pointed at its own loopback hub.
            from .combined import run_combined
            run_combined()
        else:
            from .hub import run_hub
            # An explicit hub binds the LAN by default (it exists to serve other machines);
            # an unconfigured box stays loopback. `local bind` overrides either way.
            run_hub(config.local_setting("bind", "0.0.0.0" if role == "hub" else None))
    elif cmd == "screener":
        from .screener import run_screener
        run_screener()
    elif cmd == "doctor":
        from .doctor import run_doctor
        sys.exit(run_doctor())
    elif cmd == "status":
        from .status import run_status
        run_status()
    elif cmd == "token":
        from . import auth
        worker, tok = _arg(2, "worker name: "), _arg(3, "token: ")
        conn = db.init_db()
        auth.set_token(conn, worker, tok)
        print(f"token set for worker '{worker}'")
    elif cmd == "auth":
        val = _arg(2, "auth (on/off): ")
        conn = db.init_db()
        db.set_setting(conn, "require_auth", "on" if val == "on" else "off")
        print(f"require_auth = {db.get_setting(conn, 'require_auth')}")
    elif cmd == "config":
        key, val = _arg(2, "key: "), _arg(3, "value: ")
        conn = db.init_db()
        db.set_setting(conn, key, val)
        print(f"{key} = {db.get_setting(conn, key)}")
    elif cmd == "setup-hub":
        secret = _arg(2, "dashboard password (protects the web board, blank = open): ")
        _setup_hub(secret)
        print("hub configured — role=hub, bind=0.0.0.0 (LAN), real mode, auth on"
              + (", dashboard password set." if secret
                 else ".\n  WARNING: no dashboard password — the board is open to the LAN."))
        print("\nchecking real-mode prereqs:")
        from .doctor import run_doctor
        run_doctor()
        print("\nstart it:  brew services restart outerloop   (or: outerloop service)")
        print("then pair workers on the hub's /fleet page.")
    elif cmd == "setup-both":
        secret = _arg(2, "dashboard password (protects the web board, blank = open): ")
        _setup_hub(secret, role="both")
        print("node configured — role=both (hub + co-located worker), bind=0.0.0.0 (LAN), "
              "real mode, auth on"
              + (", dashboard password set." if secret
                 else ".\n  WARNING: no dashboard password — the board is open to the LAN."))
        print("  the co-located worker + its token are provisioned automatically on start.")
        print("\nchecking real-mode prereqs:")
        from .doctor import run_doctor
        run_doctor()
        print("\nstart it:  brew services restart outerloop   (or: outerloop service)")
        print("this Mac then runs the hub and also does work itself.")
    elif cmd == "local":
        key, val = _arg(2, "key: "), _arg(3, "value: ")
        config.set_local(key, val)
        print(f"{key} = {val}  (machine-local; settings.json)")
    else:
        print("usage: python3 -m outerloop {version|init|serve|tick|hub|worker|service|setup-hub|setup-both|screener|doctor|status|auth|token|config|local}")
        print("  version                  print the installed version")
        print("  init                     create/upgrade the SQLite db")
        print("  serve [port]             single-machine web UI (default :8765)")
        print("  tick                     run one single-machine scheduler pass")
        print("  hub [host] [port]        fleet hub: API + UI + scheduler (default :8765)")
        print("  worker                   fleet worker daemon (config from OUTERLOOP_* env or `local`)")
        print("  service                  run this box's role (local 'role': hub|worker|both) — for brew services")
        print("  setup-hub [password]     configure a real LAN hub: bind 0.0.0.0, real mode, auth on, dashboard password")
        print("  setup-both [password]    like setup-hub, but this Mac is ALSO a worker (hub + co-located worker)")
        print("  screener                 market producer: files analysis tickets (OUTERLOOP_* env)")
        print("  doctor                   check real-mode prereqs (git/gh/claude); nonzero exit if blocked")
        print("  status                   show role/mode, daemon state, and (worker) hub reachability")
        print("  auth <on|off>            require per-worker bearer-token auth on the API")
        print("  token <worker> <tok>     provision a worker's bearer token")
        print("  config <key> <value>     write a hub DB settings row, e.g.:")
        print("       config notify_url https://ntfy.example.com/inbox   (decision push, ''=off)")
        print("       config intake_token <secret>                       (enables POST /api/intake)")
        print("       config ui_token <secret>                           (password-gate the dashboard, ''=open)")
        print("  local <key> <value>      write machine-local runtime config (settings.json):")
        print("       local role hub|worker|both | local bind 0.0.0.0 (LAN hub) | local fake 0 (real)")
        print("       local hub_url http://hub.local:8765 | local worker <name> | local token <tok>")


if __name__ == "__main__":
    main()
