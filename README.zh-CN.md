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

## 频道状态模型

频道状态字段描述的是两个相关但彼此独立的概念：

- `status`: 频道是否参与自动定时检查。
- `last_checked_at`: 系统最近一次尝试检查频道的时间。成功和失败都会更新这个值。
- `last_sync_at`: 最近一次频道检查成功完成，并刷新本地视频记录的时间。
- `next_check_at`: 调度器计划的下一次自动检查时间。暂停频道不会有下一次自动检查时间。
- `last_error`: 最近一次检查失败的原因，系统会尽量归类成可读的错误类型。

`active` 和 `paused` 是配置状态，不是后台任务运行状态。暂停频道不会参与定时自动检查，但当前实现仍允许手动同步。

```mermaid
stateDiagram-v2
    [*] --> 启用: 新建频道默认 active

    启用 --> 暂停: 将状态设置为 paused
    暂停 --> 启用: 将状态设置为 active

    启用 --> 同步任务: 调度器到达 next_check_at
    启用 --> 同步任务: 点击“立即同步”
    暂停 --> 同步任务: 点击“立即同步”

    暂停 --> 暂停: 调度器不会加入检查任务

    同步任务 --> 启用: 任务结束且频道仍为启用
    同步任务 --> 暂停: 任务结束且频道仍为暂停
```

每一次同步任务都会尝试列出频道视频，并写入本地数据库：

```mermaid
flowchart TD
    A["同步任务开始"] --> B["用 yt-dlp / youtube-dl 列出频道视频"]
    B --> C{"列表获取并解析成功？"}

    C -->|否| D["更新 last_checked_at\n保持 last_sync_at 不变\n写入 last_error"]
    C -->|是| E["写入或更新本地视频记录"]
    E --> F{"数据库刷新成功？"}

    F -->|否| D
    F -->|是| G["更新 last_checked_at\n更新 last_sync_at\n清空 last_error"]

    G --> H{"是否开启 auto_download？"}
    H -->|是| I["将未完成视频加入下载队列"]
    H -->|否| J["不加入下载队列"]

    I --> K["下载任务单独运行"]
    J --> K
```

简而言之，`last_checked_at` 回答“系统上次什么时候试过？”，`last_sync_at` 回答“频道列表上次什么时候成功刷新？”。视频下载是独立任务，不会更新这两个频道时间戳。

## 下载说明

- 程序会优先使用 `bin/yt-dlp.exe`
- 如果希望 `yt-dlp` 将分离的视频流和音频流自动合并成一个完整文件，请确保 `ffmpeg` 可用：可以放在项目的 `bin/ffmpeg.exe`、`bin/ffmpeg`，或放在系统 `PATH` 中
- 如果没有 `ffmpeg`，下载器会尽量回退到单文件格式，保证结果仍然包含音频和视频，但可用分辨率可能会更受限制
- 在 `bin/` 中放入或替换 `ffmpeg`，或调整系统 `PATH` 后，需要重启后端服务，下载器才会重新检测到它
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
