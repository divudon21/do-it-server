# Do It Server

yt-dlp based download server for the **Do It** Android app.

## Endpoints
- `GET /health` – health check
- `POST /api/download` – body `{ "url": "...", "format": "video|audio" }` → `{ "job_id": "..." }`
- `GET /api/status/<job_id>` – progress + status
- `GET /api/file/<job_id>` – download finished file

## Deploy on Render
Uses the included Dockerfile (ffmpeg preinstalled). Free plan supported.
