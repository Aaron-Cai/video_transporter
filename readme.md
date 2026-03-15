# Video Transporter

English documentation. 中文版请见 [README.zh-CN.md](./README.zh-CN.md)

A project scaffold for managing YouTube channels and downloading videos automatically.

## Tech Stack

- Backend: FastAPI + SQLAlchemy + APScheduler + SQLite
- Frontend: React + Vite + TypeScript
- Downloader: `bin/ytp-dl.exe`
- Preferred downloader: `bin/yt-dlp.exe`, with fallback to `bin/youtube-dl.exe`
- Python environment management: `uv`

## Implemented Features

- Create, update, and delete YouTube channels
- Trigger a one-time full sync after adding a new channel
- Poll channels on a schedule and automatically download newly discovered videos
- Web console for channel management and download record viewing

## Project Structure

```text
backend/
  app/
    api/
    services/
frontend/
bin/
data/
downloads/
```

## Start the Backend

```bash
uv sync
uv run uvicorn backend.app.main:app --reload
```

## Start the Frontend

```bash
cd frontend
npm install
npm run dev
```

In development mode, the frontend runs on `http://127.0.0.1:5173` by default and proxies API requests to the backend automatically.

## Logging

- Application and API logs: `logs/app.log`
- Download-specific logs: `logs/download.log`
- Both log streams are also printed to the backend terminal
- Logs rotate daily and are retained for 14 days by default

## Download Notes

- The app prefers `bin/yt-dlp.exe`
- If `cookies.txt` exists in the project root, it is automatically used for downloads
- A 2-second interval is applied before each download by default to avoid overly aggressive requests to YouTube
- Failed tasks can be re-queued by selecting the most recent failed `N` items, with a default of 20
- The following values can be overridden via `.env`:
  - `VIDEO_TRANSPORTER_YT_DLP_PATH`
  - `VIDEO_TRANSPORTER_YOUTUBE_DL_PATH`
  - `VIDEO_TRANSPORTER_YOUTUBE_COOKIES_PATH`
  - `VIDEO_TRANSPORTER_DOWNLOAD_INTERVAL_SECONDS`
