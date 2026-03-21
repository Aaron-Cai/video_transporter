# Video Transporter

中文说明文档。English version: [readme.md](./readme.md)

一个用于管理 YouTube 频道并自动下载视频的项目骨架。

## 技术选型

- 后端: FastAPI + SQLAlchemy + APScheduler + SQLite
- 前端: React + Vite + TypeScript
- 下载器: `bin/ytp-dl.exe`
- 优先下载器: `bin/yt-dlp.exe`，不存在时回退到 `bin/youtube-dl.exe`
- Python 环境管理: `uv`

## 已实现能力

- YouTube 频道增删改查
- 新增频道后触发一次性全量同步
- 定时轮询频道并发现新视频后自动下载
- Web Console 管理频道与查看视频下载记录

## 目录结构

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

## 后端启动

```bash
uv sync
uv run uvicorn backend.app.main:app --reload
```

## 前端启动

```bash
cd frontend
npm install
npm run dev
```

开发模式下前端默认运行在 `http://127.0.0.1:5173`，并自动代理到后端接口。

## Windows 一键启动

```powershell
.\start-dev.cmd
```

该命令会分别打开前后端终端窗口，并在启动前自动执行后端的 `uv sync` 与前端的 `npm install`，随后启动两个开发服务。

## macOS 和 Linux 一键启动

```bash
chmod +x ./start-dev.sh
./start-dev.sh
```

该命令会在当前终端中同时运行前后端，并在启动前自动执行后端的 `uv sync` 与前端的 `npm install`，随后一起启动两个开发服务。

## 日志输出

- 应用与接口日志: `logs/app.log`
- 下载专用日志: `logs/download.log`
- 两类日志都会同时输出到后端启动终端
- 日志按天滚动，默认保留 14 天

## 下载说明

- 程序会优先使用 `bin/yt-dlp.exe`
- 如果项目根目录存在 `cookies.txt`，会自动在下载时带上 cookies
- 默认使用单个下载 worker，减少并发请求
- 默认每次下载前会随机等待 8 到 20 秒，避免过于密集地请求 YouTube
- 失败任务支持按“最近失败的前 N 个”重新加入队列，默认 20 个
- 可通过 `.env` 覆盖：
  - `VIDEO_TRANSPORTER_YT_DLP_PATH`
  - `VIDEO_TRANSPORTER_YOUTUBE_DL_PATH`
  - `VIDEO_TRANSPORTER_YOUTUBE_COOKIES_PATH`
  - `VIDEO_TRANSPORTER_DOWNLOAD_INTERVAL_MIN_SECONDS`
  - `VIDEO_TRANSPORTER_DOWNLOAD_INTERVAL_MAX_SECONDS`
