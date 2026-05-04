# Water Hourglass Rate Service

Cloud-hosted Python service that subscribes to Bluesky's firehose and exposes the current "droplet rate" via HTTP for the Arduino to poll.

## Files

- `app.py` Flask service with a background thread that maintains the Bluesky firehose connection
- `requirements.txt` Python dependencies
- `Procfile` start command for Render or Heroku-style platforms
- `render.yaml` declarative Render config (set env vars, plan, etc.)

## Test locally first

```bash
cd cloud/
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000/` in a browser. You should see the dashboard with current rate, posts per second, and firehose status. Within a few seconds the firehose should connect and the numbers should start updating.

Test the Arduino endpoint:

```bash
curl http://localhost:5000/rate
```

Returns a plain text number 0-255.

## Deploy to Render

1. Push this folder to a GitHub repo (e.g., create a new repo and put the contents of `cloud/` at the root, or point Render at this subfolder).
2. Go to [render.com](https://render.com), sign in, click **New > Web Service**.
3. Connect your GitHub repo.
4. Render should auto-detect Python from `requirements.txt`. If asked, set:
   - **Runtime:** Python 3.11+
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
5. Choose **Starter plan** ($7/month) for always-on. Free tier sleeps after 15 minutes of inactivity, which breaks the firehose connection.
6. Click **Deploy**.

After deployment, your service will be at something like `https://hourglass-rate.onrender.com`. Test with:
```
https://hourglass-rate.onrender.com/rate
```

## Calibration

The default mapping (10 posts/sec to 0, 60 posts/sec to 255) lands the rate in a useful range during typical Bluesky activity. Tune via the Render dashboard's **Environment** settings if needed:

- `MIN_RATE` posts/sec at which output is 0 (slowest droplet rate)
- `MAX_RATE` posts/sec at which output is 255 (fastest droplet rate)
- `WINDOW_SEC` rolling window for averaging (longer = smoother, slower to react)
- `SMOOTHING` exponential smoothing factor 0 to 1 (higher = more responsive)

After changing env vars, Render will restart the service automatically.

## Arduino side

Your Nano 33 IoT polls `https://your-service.onrender.com/rate` every 1-2 seconds, parses the response as an integer, and uses that value to set the solenoid firing interval. See the Arduino sketch (next file) for the WiFi + HTTP polling implementation.

## Why workers=1

The firehose subscriber runs as a background thread inside the gunicorn worker process. Multiple workers would each open their own WebSocket to Bluesky, wasting connections and serving inconsistent data between workers (since they each maintain their own counter). One worker with multiple threads serves all HTTP requests fine for this use case.
