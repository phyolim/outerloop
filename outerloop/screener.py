"""A market PRODUCER: watch market signals and FILE analysis tickets into the
queue. This is a thin client of POST /api/tickets — it never touches the DB. Replace
check_signals() with your real screening/analysis (TradingView MCP, a data feed, etc.).
Tickets require [market-data, analysis] so only workers with those caps claim them; the
dedup_key keeps a repeated signal from flooding the queue.

Config via env: OUTERLOOP_HUB (hub url), OUTERLOOP_WORKER_TOKEN (this worker's token),
OUTERLOOP_SCREENER_SEC (poll interval)."""

import os
import time

from . import client, config


def check_signals():
    """Return [{title, body, dedup_key}] for conditions worth an analysis ticket.
    REPLACE THIS with your real logic. In FAKE mode it emits one demo signal so the
    producer path is testable end to end."""
    if config.FAKE:
        return [{"title": "AAPL broke 50-day support",
                 "body": "Run the breakdown playbook and summarize the setup + risk.",
                 "dedup_key": "demo-aapl-breakdown"}]
    return []  # TODO: your TradingView / data-feed screening goes here


def _file(base, token, sig):
    return client.post(base, "/api/tickets", {
        "title": sig["title"], "body": sig.get("body", ""), "type": "knowledge",
        "requires": ["market-data", "analysis"],
        "dedup_key": sig.get("dedup_key"), "source": "screener",
    }, token=token)


def run_screener_once():
    """One screening pass. Returns the list of filed ticket ids (deduped)."""
    base = os.environ.get("OUTERLOOP_HUB", "http://127.0.0.1:8765")
    token = os.environ.get("OUTERLOOP_WORKER_TOKEN")
    return [_file(base, token, s).get("id") for s in check_signals()]


def run_screener():
    base = os.environ.get("OUTERLOOP_HUB", "http://127.0.0.1:8765")
    token = os.environ.get("OUTERLOOP_WORKER_TOKEN")
    interval = int(os.environ.get("OUTERLOOP_SCREENER_SEC", "60"))
    print(f"screener -> {base} (FAKE={config.FAKE})")
    while True:
        try:
            for sig in check_signals():
                r = _file(base, token, sig)
                tag = " (dup)" if r.get("dedup") else ""
                print(f"filed: {sig['title']} -> ticket {r.get('id')}{tag}")
        except client.APIError as e:
            print(f"hub error {e.code}; retrying")
        except OSError as e:
            print(f"hub unreachable ({e}); retrying")
        time.sleep(interval)
