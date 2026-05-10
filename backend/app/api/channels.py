import html
import mimetypes
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ChannelScanType
from ..scheduler import ChannelScheduler
from ..schemas import (
    ChannelCreate,
    ChannelListItem,
    ChannelMetadataResponse,
    ChannelRead,
    ChannelUpdate,
    DownloadDeferredResponse,
    DownloadPendingResponse,
    RetryFailedResponse,
    SyncResponse,
)
from ..services.channel_service import ChannelService
from ..services.sync_service import SyncManager

router = APIRouter(prefix="/channels", tags=["channels"])


def get_sync_manager() -> SyncManager:
    return getattr(router, "sync_manager")


def get_scheduler() -> ChannelScheduler:
    return getattr(router, "scheduler")


def _get_channel_or_404(service: ChannelService, channel_id: int) -> Any:
    channel = service.get_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return channel


def _get_video_or_404(service: ChannelService, video_id: int) -> Any:
    video = service.get_video(video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


def _get_video_file_or_404(video: Any) -> Path:
    if not video.download_path:
        raise HTTPException(status_code=404, detail="Video file is not available")
    file_path = Path(video.download_path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Video file was not found on disk")
    return file_path


def _get_subtitle_file_or_404(video: Any) -> Path:
    if not video.subtitle_path:
        raise HTTPException(status_code=404, detail="Subtitle file is not available")
    file_path = Path(video.subtitle_path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Subtitle file was not found on disk")
    return file_path


def _channel_read_with_schedule(channel: Any, scheduler: ChannelScheduler) -> ChannelRead:
    item = ChannelRead.model_validate(channel)
    item.next_check_at = scheduler.get_next_check_at(channel.id)
    return item


@router.get("", response_model=list[ChannelListItem])
def list_channels(
    db: Session = Depends(get_db),
    scheduler: ChannelScheduler = Depends(get_scheduler),
) -> list[ChannelListItem]:
    channels = ChannelService(db).list_channels()
    for channel in channels:
        channel.next_check_at = scheduler.get_next_check_at(channel.id)
    return channels


@router.get("/metadata", response_model=ChannelMetadataResponse)
def get_channel_metadata(
    url: Annotated[str, Query(min_length=1, max_length=500)],
    sync_manager: Annotated[SyncManager, Depends(get_sync_manager)],
    scan_type: Annotated[ChannelScanType, Query()] = ChannelScanType.VIDEOS,
) -> ChannelMetadataResponse:
    metadata = sync_manager.youtube_dl.get_channel_metadata(url, scan_type)
    return ChannelMetadataResponse(
        name=metadata["name"],
        url=metadata["url"],
        scan_type=scan_type,
    )


@router.get("/{channel_id}", response_model=ChannelRead)
def get_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    scheduler: ChannelScheduler = Depends(get_scheduler),
) -> ChannelRead:
    service = ChannelService(db)
    channel = _get_channel_or_404(service, channel_id)
    return _channel_read_with_schedule(channel, scheduler)


@router.get("/videos/{video_id}/stream")
def stream_video(video_id: int, db: Session = Depends(get_db)) -> FileResponse:
    service = ChannelService(db)
    video = _get_video_or_404(service, video_id)
    file_path = _get_video_file_or_404(video)
    media_type, _ = mimetypes.guess_type(file_path.name)
    return FileResponse(
        path=file_path,
        media_type=media_type or "application/octet-stream",
        filename=file_path.name,
    )


@router.get("/videos/{video_id}/subtitle")
def stream_subtitle(video_id: int, db: Session = Depends(get_db)) -> FileResponse:
    service = ChannelService(db)
    video = _get_video_or_404(service, video_id)
    file_path = _get_subtitle_file_or_404(video)
    media_type, _ = mimetypes.guess_type(file_path.name)
    return FileResponse(
        path=file_path,
        media_type=media_type or "text/vtt",
        filename=file_path.name,
    )


@router.get("/videos/{video_id}/play", response_class=HTMLResponse)
def play_video(video_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    service = ChannelService(db)
    video = _get_video_or_404(service, video_id)
    file_path = _get_video_file_or_404(video)
    title = html.escape(video.title or file_path.name)
    stream_url = str(request.url_for("stream_video", video_id=video_id))
    source_url = html.escape(stream_url, quote=True)
    subtitle_path = Path(video.subtitle_path) if video.subtitle_path else None
    subtitle_url = (
        str(request.url_for("stream_subtitle", video_id=video_id))
        if subtitle_path and subtitle_path.is_file()
        else None
    )
    subtitle_track = (
        f'<track src="{html.escape(subtitle_url, quote=True)}" kind="subtitles" '
        'label="Subtitles" default />'
        if subtitle_url and subtitle_path and subtitle_path.suffix.lower() == ".vtt"
        else ""
    )
    subtitle_action = (
        f'<a href="{html.escape(subtitle_url, quote=True)}" target="_blank" '
        'rel="noreferrer">打开字幕文件</a>'
        if subtitle_url
        else ""
    )
    webpage_url = html.escape(video.webpage_url, quote=True)
    page = f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
      :root {{
        color-scheme: dark;
        font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        background:
          radial-gradient(circle at top, rgba(71, 163, 255, 0.22), transparent 38%),
          linear-gradient(180deg, #0c1118 0%, #06080d 100%);
        color: #eef4ff;
        display: grid;
        place-items: center;
      }}
      main {{
        width: min(1100px, calc(100vw - 32px));
        padding: 24px;
      }}
      h1 {{
        margin: 0 0 12px;
        font-size: clamp(1.2rem, 2vw, 1.8rem);
      }}
      p {{
        margin: 0 0 16px;
        color: #aab6cc;
        word-break: break-word;
      }}
      .player {{
        overflow: hidden;
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.12);
        background: rgba(255, 255, 255, 0.04);
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
      }}
      video {{
        display: block;
        width: 100%;
        max-height: 78vh;
        background: #000;
      }}
      .actions {{
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
        margin-top: 16px;
      }}
      a {{
        color: #7cc4ff;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>{title}</h1>
      <p>{html.escape(str(file_path))}</p>
      <div class="player">
        <video controls autoplay preload="metadata">
          <source src="{source_url}" />
          {subtitle_track}
          当前浏览器无法直接播放该视频，可使用下方链接单独打开或下载。
        </video>
      </div>
      <div class="actions">
        <a href="{source_url}" target="_blank" rel="noreferrer">直接打开视频流</a>
        {subtitle_action}
        <a href="{webpage_url}" target="_blank" rel="noreferrer">打开原始视频页面</a>
      </div>
    </main>
  </body>
</html>
"""
    return HTMLResponse(page)


@router.post("", response_model=ChannelRead, status_code=status.HTTP_201_CREATED)
def create_channel(
    payload: ChannelCreate,
    db: Session = Depends(get_db),
    sync_manager: SyncManager = Depends(get_sync_manager),
    scheduler: ChannelScheduler = Depends(get_scheduler),
) -> ChannelRead:
    service = ChannelService(db)
    try:
        channel = service.create_channel(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    scheduler.reload_jobs()
    if payload.trigger_initial_sync:
        sync_manager.submit_sync(channel.id)
    return _channel_read_with_schedule(channel, scheduler)


@router.put("/{channel_id}", response_model=ChannelRead)
def update_channel(
    channel_id: int,
    payload: ChannelUpdate,
    db: Session = Depends(get_db),
    scheduler: ChannelScheduler = Depends(get_scheduler),
) -> ChannelRead:
    service = ChannelService(db)
    channel = _get_channel_or_404(service, channel_id)
    try:
        updated = service.update_channel(channel, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    scheduler.reload_jobs()
    return _channel_read_with_schedule(updated, scheduler)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_channel(
    channel_id: int,
    delete_downloads: Annotated[bool, Query()] = False,
    db: Session = Depends(get_db),
    scheduler: ChannelScheduler = Depends(get_scheduler),
) -> Response:
    service = ChannelService(db)
    channel = _get_channel_or_404(service, channel_id)
    service.delete_channel(channel, delete_downloads=delete_downloads)
    scheduler.reload_jobs()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{channel_id}/sync", response_model=SyncResponse)
def sync_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    sync_manager: SyncManager = Depends(get_sync_manager),
) -> SyncResponse:
    service = ChannelService(db)
    _get_channel_or_404(service, channel_id)
    sync_manager.submit_sync(channel_id)
    return SyncResponse(detail="Channel sync started")


@router.post("/{channel_id}/retry-failed", response_model=RetryFailedResponse)
def retry_failed_downloads(
    channel_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    sync_manager: SyncManager = Depends(get_sync_manager),
) -> RetryFailedResponse:
    service = ChannelService(db)
    _get_channel_or_404(service, channel_id)
    queued_count = sync_manager.retry_failed_downloads(channel_id, limit=limit)
    return RetryFailedResponse(
        detail="Failed video downloads queued",
        queued_count=queued_count,
        limit=limit,
    )


@router.post("/{channel_id}/download-pending", response_model=DownloadPendingResponse)
def download_pending_videos(
    channel_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    sync_manager: SyncManager = Depends(get_sync_manager),
) -> DownloadPendingResponse:
    service = ChannelService(db)
    _get_channel_or_404(service, channel_id)
    queued_count = sync_manager.download_pending_videos(channel_id, limit=limit)
    return DownloadPendingResponse(
        detail="Pending video downloads queued",
        queued_count=queued_count,
        limit=limit,
    )


@router.post("/{channel_id}/download-deferred", response_model=DownloadDeferredResponse)
def download_deferred_videos(
    channel_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    sync_manager: SyncManager = Depends(get_sync_manager),
) -> DownloadDeferredResponse:
    service = ChannelService(db)
    _get_channel_or_404(service, channel_id)
    queued_count = sync_manager.download_deferred_videos(channel_id, limit=limit)
    return DownloadDeferredResponse(
        detail="Deferred video downloads queued",
        queued_count=queued_count,
        limit=limit,
    )
