"""CLI: python3 -m inbox {init|serve|tick}"""

import sys

from . import config, db


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
    elif cmd == "coordinator":
        from .coordinator import run_coordinator
        host = sys.argv[2] if len(sys.argv) > 2 else None
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 8765
        run_coordinator(host, port)
    elif cmd == "worker":
        from .worker import run_worker
        run_worker()
    elif cmd == "screener":
        from .screener import run_screener
        run_screener()
    elif cmd == "set-token":
        from . import auth
        conn = db.init_db()
        auth.set_token(conn, sys.argv[2], sys.argv[3])
        print(f"token set for device '{sys.argv[2]}'")
    elif cmd == "set-auth":
        conn = db.init_db()
        db.set_setting(conn, "require_auth", "on" if sys.argv[2] == "on" else "off")
        print(f"require_auth = {db.get_setting(conn, 'require_auth')}")
    elif cmd == "set":
        conn = db.init_db()
        db.set_setting(conn, sys.argv[2], sys.argv[3])
        print(f"{sys.argv[2]} = {db.get_setting(conn, sys.argv[2])}")
    else:
        print("usage: python3 -m inbox {version|init|serve|tick|coordinator|worker|set-token|set-auth|set}")
        print("  version                  print the installed version")
        print("  init                     create/upgrade the SQLite db")
        print("  serve                    single-machine web UI (default :8765)")
        print("  tick                     one single-machine scheduler tick")
        print("  coordinator [host] [port]  fleet hub: API + UI + scheduler")
        print("  worker                   fleet worker daemon (INBOX_* env)")
        print("  screener                 mini producer: files analysis tickets (INBOX_* env)")
        print("  set-token <device> <tok> provision a device's bearer token")
        print("  set-auth <on|off>        require bearer-token auth on the API")
        print("  set <key> <value>        write a settings row, e.g.:")
        print("       set notify_url https://ntfy.example.com/inbox   (decision push, ''=off)")
        print("       set intake_token <secret>                       (enables POST /api/intake)")


if __name__ == "__main__":
    main()
