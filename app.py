"""
Water Hourglass: Cloudflare Radar HTTP Traffic to Droplet Rate Service
=======================================================================
Runs on Render. Polls Cloudflare Radar API every few minutes for current
internet HTTP traffic levels (normalized against baseline), maps to a
0-255 droplet rate, exposes via HTTP endpoint that the Arduino polls.

Cloudflare Radar publishes near-real-time aggregate internet traffic data
that flows through their network (~20% of all web traffic). Values are
normalized: 100 means "typical for this time of day," above 100 means
busier than usual, below 100 means quieter.

Endpoints (unchanged from previous Bluesky version):
  GET /         html landing page
  GET /health   JSON health check
  GET /rate     plain text current rate (e.g., "127"), Arduino-friendly
  GET /status   full JSON status

Required environment variable:
  CLOUDFLARE_TOKEN   Cloudflare API token with "Account Analytics: Read"
                     permission. Free Cloudflare account gives access.

Optional environment variables:
  PORT               port to listen on (Render sets this)
  POLL_INTERVAL_SEC  how often to fetch from Radar (default 300, every 5 min)
  MIN_VALUE          normalized traffic value mapped to droplet rate 0
                     (default 70, meaning "30% below baseline")
  MAX_VALUE          normalized traffic value mapped to droplet rate 255
                     (default 130, meaning "30% above baseline")
  SMOOTHING          exponential smoothing factor 0 to 1 (default 0.3)

Local run:
  pip install -r requirements.txt
  CLOUDFLARE_TOKEN=your_token_here python app.py
  curl http://localhost:5000/rate
"""

import os
import time
import threading
import json
import encodings.idna  # noqa: F401  force-load to avoid threaded LookupError on Render

import requests
from flask import Flask, jsonify

RADAR_URL = "https://api.cloudflare.com/client/v4/radar/http/timeseries"

CLOUDFLARE_TOKEN = os.environ.get("CLOUDFLARE_TOKEN", "")
POLL_INTERVAL_SEC = float(os.environ.get("POLL_INTERVAL_SEC", "300"))
MIN_VALUE = float(os.environ.get("MIN_VALUE", "70"))
MAX_VALUE = float(os.environ.get("MAX_VALUE", "130"))
SMOOTHING = float(os.environ.get("SMOOTHING", "0.3"))

state = {
    "lock": threading.Lock(),
    "last_value": 100.0,
    "last_fetch_at": 0.0,
    "last_fetch_success": False,
    "smoothed_rate": 0.0,
    "started_at": time.time(),
    "fetch_count": 0,
    "fetch_errors": 0,
    "last_error": "",
}


# ============ Cloudflare Radar Poller ============

def fetch_radar_value():
    """Hit Cloudflare Radar timeseries, return latest normalized traffic value."""
    if not CLOUDFLARE_TOKEN:
        raise RuntimeError("CLOUDFLARE_TOKEN environment variable not set")

    headers = {"Authorization": f"Bearer {CLOUDFLARE_TOKEN}"}
    params = {
        "dateRange": "1d",
        "aggInterval": "15m",
    }
    r = requests.get(RADAR_URL, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    if not data.get("success"):
        errors = data.get("errors", [])
        raise RuntimeError(f"Radar API error: {errors}")

    result = data.get("result", {})
    serie = result.get("serie_0", {})
    values = serie.get("values", [])

    if not values:
        raise RuntimeError("Radar response had no values")

    # Latest value, normalized to baseline (~100)
    return float(values[-1])


def poll_loop():
    print("[radar] poll thread starting", flush=True)
    while True:
        try:
            value = fetch_radar_value()
            with state["lock"]:
                state["last_value"] = value
                state["last_fetch_at"] = time.time()
                state["last_fetch_success"] = True
                state["fetch_count"] += 1
                state["last_error"] = ""
            print(f"[radar] traffic level: {value:.2f} (baseline = 100)", flush=True)
        except Exception as e:
            with state["lock"]:
                state["last_fetch_success"] = False
                state["fetch_errors"] += 1
                state["last_error"] = f"{type(e).__name__}: {e}"
            print(f"[radar] fetch error: {e}", flush=True)
        time.sleep(POLL_INTERVAL_SEC)


# ============ Lazy Thread Starter ============

_poll_thread = None
_poll_lock = threading.Lock()


def ensure_poll_running():
    """Start the polling thread in the current process if not already running."""
    global _poll_thread
    if _poll_thread is not None and _poll_thread.is_alive():
        return
    with _poll_lock:
        if _poll_thread is not None and _poll_thread.is_alive():
            return
        print(f"[startup] spawning radar poll thread in pid={os.getpid()}", flush=True)
        _poll_thread = threading.Thread(target=poll_loop, daemon=True)
        _poll_thread.start()


# ============ Rate Calculation ============

def compute_rate():
    """Map current traffic value to 0-255 droplet rate."""
    with state["lock"]:
        value = state["last_value"]

    if value <= MIN_VALUE:
        target = 0
    elif value >= MAX_VALUE:
        target = 255
    else:
        normalized = (value - MIN_VALUE) / (MAX_VALUE - MIN_VALUE)
        target = int(normalized * 255)

    with state["lock"]:
        state["smoothed_rate"] = (
            SMOOTHING * target + (1 - SMOOTHING) * state["smoothed_rate"]
        )
        smoothed = state["smoothed_rate"]

    return int(smoothed), value


# ============ Flask App ============

app = Flask(__name__)


@app.before_request
def _before_request():
    ensure_poll_running()


@app.route("/")
def root():
    rate_value, value = compute_rate()
    with state["lock"]:
        success = state["last_fetch_success"]
        last_fetch_at = state["last_fetch_at"]
        fetch_count = state["fetch_count"]
        fetch_errors = state["fetch_errors"]
        last_error = state["last_error"]
        uptime = int(time.time() - state["started_at"])

    age = int(time.time() - last_fetch_at) if last_fetch_at else None
    age_str = f"{age}s ago" if age is not None else "never"

    html = f"""<!DOCTYPE html>
<html><head><title>Water Hourglass: Rate Service</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 640px; margin: 40px auto; padding: 20px; }}
  h1 {{ color: #246; }}
  .stat {{ font-size: 24pt; font-weight: 600; }}
  .label {{ color: #666; font-size: 11pt; text-transform: uppercase; letter-spacing: 0.05em; }}
  .row {{ margin: 16px 0; }}
  .ok {{ color: #2a8; }}
  .bad {{ color: #c33; }}
  code {{ background: #f3f3f3; padding: 2px 6px; border-radius: 3px; }}
  .err {{ background: #fee; border-left: 3px solid #c33; padding: 10px; margin: 16px 0; font-family: monospace; font-size: 10pt; }}
</style></head>
<body>
<h1>Water Hourglass Rate Service</h1>
<div class="row">
  <div class="label">Current droplet rate (0-255)</div>
  <div class="stat">{rate_value}</div>
</div>
<div class="row">
  <div class="label">Cloudflare Radar HTTP traffic (100 = baseline)</div>
  <div class="stat">{value:.1f}</div>
</div>
<div class="row">
  <div class="label">Last fetch</div>
  <div class="stat {'ok' if success else 'bad'}">{'success' if success else 'failed'} ({age_str})</div>
</div>
<div class="row">
  <div class="label">Uptime / fetches / errors</div>
  <div>{uptime}s / {fetch_count} / {fetch_errors}</div>
</div>
{f'<div class="err">{last_error}</div>' if last_error else ''}
<hr>
<p>Endpoints: <code>/rate</code> (plain text rate for Arduino), <code>/status</code> (JSON), <code>/health</code></p>
</body></html>"""
    return html


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "last_fetch_success": state["last_fetch_success"],
    })


@app.route("/rate")
def rate():
    """Plain text current rate. Arduino reads this."""
    rate_value, _ = compute_rate()
    return str(rate_value), 200, {"Content-Type": "text/plain"}


@app.route("/status")
def status():
    rate_value, value = compute_rate()
    with state["lock"]:
        return jsonify({
            "rate": rate_value,
            "traffic_value": round(value, 2),
            "last_fetch_success": state["last_fetch_success"],
            "last_fetch_at": state["last_fetch_at"],
            "last_fetch_age_seconds": int(time.time() - state["last_fetch_at"]) if state["last_fetch_at"] else None,
            "fetch_count": state["fetch_count"],
            "fetch_errors": state["fetch_errors"],
            "last_error": state["last_error"],
            "uptime_seconds": int(time.time() - state["started_at"]),
            "config": {
                "poll_interval_sec": POLL_INTERVAL_SEC,
                "min_value": MIN_VALUE,
                "max_value": MAX_VALUE,
                "smoothing": SMOOTHING,
            },
        })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"Starting on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port)
