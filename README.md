# Water Hourglass Rate Service

Cloud-hosted Python service that polls Cloudflare Radar for current internet HTTP traffic levels and exposes the resulting "droplet rate" via HTTP for the Arduino to poll.

## What this measures

Cloudflare Radar publishes near-real-time aggregate traffic data flowing through their network (~20% of all web traffic). The values are normalized: 100 means "typical for this time of day," above 100 means busier than usual, below 100 means quieter. The piece reads this and translates it into the rate at which water droplets are released.

Conceptually: when the global internet is more active, the hourglass fills faster. When it's quieter, the hourglass slows.

**Note on lag:** Cloudflare Radar updates every ~15 minutes, not in real time. The piece responds slowly. For an art installation, this fits the conceptual frame ("the rhythm of the internet") rather than ticking second-by-second.

## Files

- `app.py` Flask service with a background polling thread
- `requirements.txt` Python dependencies
- `Procfile` start command for Render
- `render.yaml` declarative Render config
- `runtime.txt` pins Python to 3.11.9

## Setup: Get a Cloudflare API token

1. Sign up for a free Cloudflare account at [cloudflare.com](https://cloudflare.com).
2. Go to **My Profile > API Tokens > Create Token**.
3. Use the **Custom token** template. Name it "Hourglass Radar."
4. Permissions: select **Account > Account Analytics > Read**.
5. Account Resources: All accounts (or your specific account).
6. Click **Continue to summary**, then **Create Token**.
7. Copy the token. You will not see it again. Save it for the next step.

## Test locally first

```bash
cd cloud/
pip install -r requirements.txt
CLOUDFLARE_TOKEN=your_token_here python app.py
```

Open `http://localhost:5000/` in a browser. You should see the dashboard. Within ~5 minutes the first fetch should succeed and traffic value should appear.

Test the Arduino endpoint:
```bash
curl http://localhost:5000/rate
```

Returns a plain text number 0-255.

## Deploy to Render

1. Push this folder's contents to a GitHub repo.
2. Go to [render.com](https://render.com), click **New > Web Service**, connect your GitHub repo.
3. Render auto-detects Python. Confirm:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
4. Add environment variable: `CLOUDFLARE_TOKEN` = your token from above.
5. Choose **Starter plan** ($7/month) for always-on.
6. Click **Deploy**.

After deployment, test with:
```
https://your-service.onrender.com/rate
```

## Calibration

Default mapping: traffic value 70 (30% below baseline) maps to droplet rate 0, traffic value 130 (30% above baseline) maps to droplet rate 255. Tune via Render dashboard's environment settings:

- `MIN_VALUE` traffic level at which output is 0 (default 70)
- `MAX_VALUE` traffic level at which output is 255 (default 130)
- `POLL_INTERVAL_SEC` how often to fetch from Radar (default 300, every 5 min)
- `SMOOTHING` exponential smoothing factor 0 to 1 (default 0.3)

After changing env vars, Render will restart the service automatically.

## Arduino side

Your Nano 33 IoT polls `https://your-service.onrender.com/rate` every 1-2 seconds, parses the response as an integer, and uses that value to set the solenoid firing interval. The Arduino sees a smoothly changing 0-255 value, even though the underlying Cloudflare data updates only every 15 minutes (the smoothing layer interpolates).

## Why workers=1

The poller runs as a background thread inside the gunicorn worker process. Multiple workers would each fire separate Cloudflare API calls, wasting your rate limit and serving inconsistent data between workers. One worker with multiple HTTP-handling threads is plenty.

## Wall-text notes for the install

The conceptual frame shifts from "social media activity" to "internet traffic." Worth mentioning: Cloudflare carries ~20% of all web traffic, so this is a credible proxy for "the internet right now." The exact metric is normalized HTTP request volume against a rolling baseline of typical traffic patterns.
