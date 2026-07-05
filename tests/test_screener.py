# Self-contained: the mini screener producer files (deduped) analysis tickets. FAKE.
import os, sys, atexit, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OUTERLOOP_FAKE", "1")
_TMP = tempfile.mkdtemp(prefix="inbox-screener-")
os.environ["OUTERLOOP_HOME"] = _TMP
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
from outerloop import db as _bdb
_bdb.init_db()
# --- test body ---
import json, threading, time
from http.server import ThreadingHTTPServer
from outerloop import db
from outerloop.hub import HubHandler
from outerloop import screener

PORT = 8802
os.environ["OUTERLOOP_HUB"] = f"http://127.0.0.1:{PORT}"
srv = ThreadingHTTPServer(("127.0.0.1", PORT), HubHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)

first = screener.run_screener_once()
again = screener.run_screener_once()
assert first and first[0], "screener should file a ticket"
assert again == first, f"repeated signal must dedup to the same ticket id ({again} != {first})"
print(f"OK screener filed ticket {first[0]} and deduped the repeat")

c = db.connect()
t = c.execute("SELECT type, requires, prefer, dedup_key FROM ticket WHERE id=?", (first[0],)).fetchone()
c.close()
assert json.loads(t["requires"]) == ["market-data", "analysis"], t["requires"]
assert t["prefer"] is None and t["dedup_key"]
print("OK filed ticket requires [market-data, analysis] -> only capable workers can claim it")
print("\n=== SCREENER TEST PASSED ===")
srv.shutdown()
