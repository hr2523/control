"""
Water Hourglass: Bluesky Firehose to Droplet Rate Service (Cloud)
==================================================================
Runs on Render (or any Python host with always-on capability).

Subscribes to Bluesky's public Jetstream firehose, counts posts per
second in a rolling window, maps to a 0-255 droplet rate, exposes
that rate via an HTTP endpoint that the Arduino Nano 33 IoT polls.

Endpoints:
  GET /         html landing page
  GET /health   JSON health check (firehose connection status)
  GET /rate     plain text current rate (e.g., "127"), Arduino-friendly
  GET /status   full JSON status for debugging or dashboards

Environment variables (all optional, sensible defaults):
  PORT          port to listen on (Render sets this automatically)
  MIN_RATE      posts/sec that maps to droplet rate 0 (default 10)
  MAX_RATE      posts/sec that maps to droplet rate 255 (default 60)
  WINDOW_SEC    rolling window for rate computation (default 10)
  SMOOTHING     exponential smoothing factor 0 to 1 (default 0.3)

Local run:
  pip install -r requirements.txt
  python app.py
  (visit http://localhost:5000/rate)

Production run (Render uses this via Procfile):
  gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4
"""

import os
import time
import threading
import json
import encodings.idna  # noqa: F401  force-load to avoid threaded LookupError on Render
from collections import deque

from flask import Flask, jsonify
import websocket

JETSTREAM_URL = (
    "wss://jetstream2.us-east.bsky.network/subscribe"
    "?wantedCollections=app.bsky.feed.post"
)

# Configuration via environment variables, with defaults
WINDOW_SEC = float(os.environ.get("WINDOW_SEC", "10"))
MIN_RATE = float(os.environ.get("MIN_RATE", "10"))
MAX_RATE = float(os.environ.get("MAX_RATE", "60"))
SMOOTHING = float(os.environ.get("SMOOTHING", "0.3"))

# Shared mutable state. Use lock when reading or writing.
state = {
    "timestamps": deque(),
    "lock": threading.Lock(),
    "connected": False,
    "total_events": 0,
    "post_events": 0,
    "smoothed_rate": 0.0,
    "last_pps": 0.0,
    "started_at": time.time(),
}


# ============ Bluesky Jetstream Subscriber ============

def on_message(ws, message):
    try:
        event = json.loads(message)
    except (json.JSONDecodeError, AttributeError):
        return

    with state["lock"]:
        state["total_events"] += 1
        if event.get("kind") == "commit":
            commit = event.get("commit", {})
            if (commit.get("operation") == "create"
                    and commit.get("collection") == "app.bsky.feed.post"):
                state["timestamps"].append(time.time())
                state["post_events"] += 1


def on_open(ws):
    state["connected"] = True
    print("[firehose] connected", flush=True)


def on_close(ws, code, msg):
    state["connected"] = False
    print(f"[firehose] closed: {code} {msg}", flush=True)


def on_error(ws, error):
    print(f"[firehose] error: {error}", flush=True)


def firehose_loop():
    """Background thread. Maintains WebSocket connection, reconnects on failure."""
    print("[firehose] thread starting", flush=True)
    attempt = 0
    while True:
        attempt += 1
        try:
            print(f"[firehose] attempt #{attempt}: connecting to {JETSTREAM_URL}", flush=True)
            ws = websocket.WebSocketApp(
                JETSTREAM_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
            print(f"[firehose] run_forever returned (attempt #{attempt})", flush=True)
        except Exception as e:
            print(f"[firehose] exception on attempt #{attempt}: {type(e).__name__}: {e}", flush=True)
        time.sleep(2)


# Start firehose thread on module import (so gunicorn picks it up)
print("[startup] spawning firehose thread", flush=True)
threading.Thread(target=firehose_loop, daemon=True).start()


# ============ Rate Calculation ============

def compute_rate():
    """Compute current droplet rate (0-255) and posts-per-second."""
    with state["lock"]:
        now = time.time()
        cutoff = now - WINDOW_SEC
        while state["timestamps"] and state["timestamps"][0] < cutoff:
            state["timestamps"].popleft()
        pps = len(state["timestamps"]) / WINDOW_SEC

    if pps <= MIN_RATE:
        target = 0
    elif pps >= MAX_RATE:
        target = 255
    else:
        normalized = (pps - MIN_RATE) / (MAX_RATE - MIN_RATE)
        target = int(normalized * 255)

    with state["lock"]:
        state["smoothed_rate"] = (
            SMOOTHING * target + (1 - SMOOTHING) * state["smoothed_rate"]
        )
        state["last_pps"] = pps
        smoothed = state["smoothed_rate"]

    return int(smoothed), pps


# ============ Flask App ============

app = Flask(__name__)


@app.route("/")
def root():
    rate_value, pps = compute_rate()
    connected = state["connected"]
    uptime = int(time.time() - state["started_at"])
    html = f"""<!DOCTYPE html>
<html><head><title>Water Hourglass: Rate Service</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; }}
  h1 {{ color: #246; }}
  .stat {{ font-size: 24pt; font-weight: 600; }}
  .label {{ color: #666; font-size: 11pt; text-transform: uppercase; letter-spacing: 0.05em; }}
  .row {{ margin: 16px 0; }}
  .ok {{ color: #2a8; }}
  .bad {{ color: #c33; }}
  code {{ background: #f3f3f3; padding: 2px 6px; border-radius: 3px; }}
</style></head>
<body>
<h1>Water Hourglass Rate Service</h1>
<div class="row">
  <div class="label">Current droplet rate (0-255)</div>
  <div class="stat">{rate_value}</div>
</div>
<div class="row">
  <div class="label">Bluesky posts per second</div>
  <div class="stat">{pps:.1f}</div>
</div>
<div class="row">
  <div class="label">Firehose status</div>
  <div class="stat {'ok' if connected else 'bad'}">{'connected' if connected else 'disconnected'}</div>
</div>
<div class="row">
  <div class="label">Uptime</div>
  <div>{uptime} seconds</div>
</div>
<hr>
<p>Endpoints: <code>/rate</code> (plain text rate for Arduino), <code>/status</code> (JSON), <code>/health</code></p>
</body></html>"""
    return html


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "firehose_connected": state["connected"],
    })


@app.route("/rate")
def rate():
    """Return current rate as plain text. Arduino reads this."""
    rate_value, _ = compute_rate()
    return str(rate_value), 200, {"Content-Type": "text/plain"}


@app.route("/status")
def status():
    rate_value, pps = compute_rate()
    return jsonify({
        "rate": rate_value,
        "posts_per_second": round(pps, 2),
        "firehose_connected": state["connected"],
        "total_events": state["total_events"],
        "post_events": state["post_events"],
        "uptime_seconds": int(time.time() - state["started_at"]),
        "config": {
            "window_sec": WINDOW_SEC,
            "min_rate": MIN_RATE,
            "max_rate": MAX_RATE,
            "smoothing": SMOOTHING,
        },
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"Starting on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port)
