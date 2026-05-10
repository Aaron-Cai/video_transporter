from __future__ import annotations

import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import Channel, ChannelStatus, DownloadStatus
from .channel_service import ChannelService
from .youtube_dl import YoutubeDlClient

logger = logging.getLogger("video_transporter.sync")

AUTO_DOWNLOADABLE_STATUSES = {
    DownloadStatus.PENDING,
    DownloadStatus.FAILED,
    DownloadStatus.SKIPPED,
}

YOUTUBE_ACCESS_LIMIT_TOKENS = (
    "http error 429",
    "too many requests",
    "sign in to confirm",
    "not a bot",
    "cookies",
)


def _called_process_output(exc: subprocess.CalledProcessError) -> str:
    return "\n".join(
        part.strip()
        for part in (exc.stderr or "", exc.stdout or "")
        if part and part.strip()
    )


def is_youtube_access_limited(exc: Exception) -> bool:
    if not isinstance(exc, subprocess.CalledProcessError):
        return False
    output = _called_process_output(exc).lower()
    return any(token in output for token in YOUTUBE_ACCESS_LIMIT_TOKENS)


def classify_sync_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"下载器缺失：{exc}"

    if isinstance(exc, json.JSONDecodeError):
        return f"输出解析失败：下载器没有返回合法的频道列表 JSON（{exc}）"

    if isinstance(exc, subprocess.CalledProcessError):
        output = _called_process_output(exc)
        detail = output[:1200] if output else str(exc)
        lowered = output.lower()

        if "http error 429" in lowered or "too many requests" in lowered:
            category = "请求过于频繁"
        elif any(
            token in lowered for token in ("sign in", "login", "cookie", "cookies", "account")
        ):
            category = "登录或 Cookies 限制"
        elif any(
            token in lowered
            for token in ("captcha", "not a bot", "confirm you're not a bot", "verification")
        ):
            category = "YouTube 验证或访问限制"
        elif any(
            token in lowered
            for token in ("unsupported url", "no suitable extractor", "invalid url")
        ):
            category = "频道 URL 无效"
        elif any(
            token in lowered
            for token in ("not available", "unavailable", "private", "deleted", "terminated")
        ):
            category = "频道不可访问"
        elif any(
            token in lowered
            for token in (
                "timed out",
                "timeout",
                "connection",
                "network",
                "dns",
                "name resolution",
                "temporary failure",
            )
        ):
            category = "网络访问失败"
        else:
            category = "下载器执行失败"

        return f"{category}：{detail}"

    return f"程序内部异常：{exc}"


class SyncManager:
    def __init__(self, youtube_dl: YoutubeDlClient) -> None:
        self.youtube_dl = youtube_dl
        self.sync_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="channel-sync")
        self.download_executors: dict[int, tuple[int, ThreadPoolExecutor]] = {}
        self.download_executors_lock = Lock()

    def submit_sync(self, channel_id: int) -> None:
        logger.info("Queueing channel sync: channel_id=%s", channel_id)
        self.sync_executor.submit(self._sync_channel, channel_id)

    def submit_video_download(
        self,
        channel_id: int,
        video_id: int,
        *,
        allow_paused: bool = False,
    ) -> None:
        logger.info("Queueing video download: channel_id=%s video_id=%s", channel_id, video_id)
        db = SessionLocal()
        try:
            channel = ChannelService(db).get_channel(channel_id)
            if channel is None:
                logger.warning(
                    "Video download skipped because channel does not exist when queueing: "
                    "channel_id=%s video_id=%s",
                    channel_id,
                    video_id,
                )
                return
            executor = self._get_download_executor(channel_id, channel.download_concurrency)
            executor.submit(self._download_video_by_id, channel_id, video_id, allow_paused)
        finally:
            db.close()

    def shutdown(self) -> None:
        self.sync_executor.shutdown(wait=False, cancel_futures=False)
        with self.download_executors_lock:
            executors = list(self.download_executors.values())
            self.download_executors.clear()
        for _, executor in executors:
            executor.shutdown(wait=False, cancel_futures=False)

    def recover_interrupted_downloads(self) -> int:
        db = SessionLocal()
        try:
            count = ChannelService(db).mark_interrupted_downloads_failed()
            if count:
                logger.warning("Marked interrupted downloads as failed: count=%s", count)
            return count
        finally:
            db.close()

    def repair_completed_download_paths(self) -> int:
        db = SessionLocal()
        try:
            service = ChannelService(db)
            repaired_count = 0
            for video in service.list_completed_videos():
                if video.download_path and Path(video.download_path).is_file():
                    subtitle_path = self.youtube_dl.resolve_subtitle_path(
                        video.channel.name,
                        video.youtube_video_id,
                        video.download_path,
                    )
                    if subtitle_path and str(subtitle_path) != video.subtitle_path:
                        service.update_video_download_path(
                            video,
                            video.download_path,
                            str(subtitle_path),
                        )
                        repaired_count += 1
                    continue
                resolved_path = self.youtube_dl.resolve_download_path(
                    video.channel.name,
                    video.youtube_video_id,
                    video.download_path,
                )
                if resolved_path is None:
                    continue
                subtitle_path = self.youtube_dl.resolve_subtitle_path(
                    video.channel.name,
                    video.youtube_video_id,
                    resolved_path,
                )
                service.update_video_download_path(
                    video,
                    str(resolved_path),
                    str(subtitle_path) if subtitle_path else None,
                )
                repaired_count += 1
            if repaired_count:
                logger.warning("Repaired completed download paths: count=%s", repaired_count)
            return repaired_count
        finally:
            db.close()

    def retry_failed_downloads(self, channel_id: int, *, limit: int = 20) -> int:
        db = SessionLocal()
        try:
            service = ChannelService(db)
            failed_videos = service.list_failed_videos(channel_id, limit=limit)
            for video in failed_videos:
                self.submit_video_download(channel_id, video.id, allow_paused=True)
            logger.info(
                "Queued failed downloads retry: channel_id=%s count=%s limit=%s",
                channel_id,
                len(failed_videos),
                limit,
            )
            return len(failed_videos)
        finally:
            db.close()

    def download_pending_videos(self, channel_id: int, *, limit: int = 20) -> int:
        db = SessionLocal()
        try:
            service = ChannelService(db)
            pending_videos = service.list_pending_videos(channel_id, limit=limit)
            for video in pending_videos:
                self.submit_video_download(channel_id, video.id, allow_paused=True)
            logger.info(
                "Queued pending downloads: channel_id=%s count=%s limit=%s",
                channel_id,
                len(pending_videos),
                limit,
            )
            return len(pending_videos)
        finally:
            db.close()

    def download_deferred_videos(self, channel_id: int, *, limit: int = 20) -> int:
        db = SessionLocal()
        try:
            service = ChannelService(db)
            deferred_videos = service.list_deferred_videos(channel_id, limit=limit)
            for video in deferred_videos:
                self.submit_video_download(channel_id, video.id, allow_paused=True)
            logger.info(
                "Queued deferred downloads: channel_id=%s count=%s limit=%s",
                channel_id,
                len(deferred_videos),
                limit,
            )
            return len(deferred_videos)
        finally:
            db.close()

    def _sync_channel(self, channel_id: int) -> None:
        db = SessionLocal()
        try:
            service = ChannelService(db)
            channel = service.get_channel(channel_id)
            if channel is None:
                logger.warning(
                    "Channel sync skipped because channel does not exist: channel_id=%s",
                    channel_id,
                )
                return
            logger.info("Starting channel sync: channel_id=%s name=%s", channel.id, channel.name)
            entries = self.youtube_dl.list_channel_videos(channel.url, channel.scan_type)
            is_initial_sync = channel.last_sync_at is None
            new_video_count = 0
            published_at_backfill_count = 0
            for index, entry in enumerate(entries):
                initial_status = DownloadStatus.PENDING
                if is_initial_sync and index >= channel.initial_download_limit:
                    initial_status = DownloadStatus.DEFERRED
                video, created = service.upsert_video(
                    channel=channel,
                    initial_status=initial_status,
                    **entry,
                )
                if created:
                    new_video_count += 1
                if (
                    video.published_at is None
                    and published_at_backfill_count
                    < settings.publish_date_backfill_limit_per_sync
                ):
                    try:
                        published_at = self.youtube_dl.get_video_published_at(video.webpage_url)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "Video published date backfill failed: video_id=%s",
                            video.id,
                            exc_info=True,
                        )
                        published_at = None
                    if published_at is not None:
                        video, _ = service.upsert_video(
                            channel=channel,
                            initial_status=initial_status,
                            published_at=published_at,
                            youtube_video_id=video.youtube_video_id,
                            title=video.title,
                            webpage_url=video.webpage_url,
                        )
                    published_at_backfill_count += 1
                if channel.auto_download and video.status in AUTO_DOWNLOADABLE_STATUSES:
                    self.submit_video_download(channel.id, video.id)
            service.mark_channel_synced(channel)
            logger.info(
                "Channel sync completed: channel_id=%s discovered=%s newly_seen=%s",
                channel.id,
                len(entries),
                new_video_count,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Channel sync failed for channel_id=%s", channel_id)
            channel = ChannelService(db).get_channel(channel_id)
            if channel is not None:
                ChannelService(db).mark_channel_checked(channel, error=classify_sync_error(exc))
        finally:
            db.close()

    def _download_video_by_id(
        self,
        channel_id: int,
        video_id: int,
        allow_paused: bool = False,
    ) -> None:
        db = SessionLocal()
        try:
            channel = ChannelService(db).get_channel(channel_id)
            if channel is None:
                logger.warning(
                    "Video download skipped because channel does not exist: "
                    "channel_id=%s video_id=%s",
                    channel_id,
                    video_id,
                )
                return
            self._download_video(db, channel, video_id, allow_paused=allow_paused)
        finally:
            db.close()

    def _download_video(
        self,
        db: Session,
        channel: Channel,
        video_id: int,
        *,
        allow_paused: bool = False,
    ) -> None:
        service = ChannelService(db)
        channel_id = channel.id
        channel = service.get_channel(channel_id)
        if channel is None:
            logger.warning(
                "Video download skipped because channel vanished: channel_id=%s",
                channel_id,
            )
            return
        if channel.status == ChannelStatus.PAUSED and not allow_paused:
            logger.info(
                "Video download skipped because channel is paused: channel_id=%s video_id=%s",
                channel.id,
                video_id,
            )
            return
        video = service.get_video(video_id)
        if video is None:
            logger.warning(
                "Video download skipped because video does not exist: channel_id=%s video_id=%s",
                channel.id,
                video_id,
            )
            return
        if video.status == DownloadStatus.COMPLETED:
            logger.info(
                "Video download skipped because already completed: channel_id=%s video_id=%s",
                channel.id,
                video_id,
            )
            return
        if video.status == DownloadStatus.DOWNLOADING:
            logger.info(
                "Video download skipped because already downloading: channel_id=%s video_id=%s",
                channel.id,
                video_id,
            )
            return
        try:
            logger.info(
                "Starting managed download: channel_id=%s video_id=%s title=%s",
                channel.id,
                video.id,
                video.title,
            )
            service.update_video_status(video, status=DownloadStatus.DOWNLOADING)
            download_result = self.youtube_dl.download_video(
                video.webpage_url,
                channel.name,
                preferred_resolution=channel.preferred_resolution,
                prefer_hdr=channel.prefer_hdr,
            )
            download_path = download_result.media_path
            subtitle_path = download_result.subtitle_path
            status = DownloadStatus.COMPLETED if download_path else DownloadStatus.SKIPPED
            service.update_video_status(
                video,
                status=status,
                download_path=str(download_path) if download_path else None,
                subtitle_path=str(subtitle_path) if subtitle_path else None,
            )
            logger.info(
                "Managed download finished: channel_id=%s video_id=%s "
                "status=%s path=%s subtitle=%s",
                channel.id,
                video.id,
                status.value,
                download_path,
                subtitle_path,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Video download failed for video_id=%s", video_id)
            error_message = classify_sync_error(exc)
            if is_youtube_access_limited(exc):
                service.update_video_status(
                    video,
                    status=DownloadStatus.DEFERRED,
                    error_message=error_message,
                )
                service.pause_channel(
                    channel,
                    error=(
                        "YouTube 暂时限制访问，已暂停该频道并延后剩余下载；"
                        "请稍后重试或配置 cookies.txt 后重新启用。"
                    ),
                )
                logger.warning(
                    "Paused channel after YouTube access limit: channel_id=%s video_id=%s",
                    channel.id,
                    video_id,
                )
                return
            service.update_video_status(
                video,
                status=DownloadStatus.FAILED,
                error_message=error_message,
            )

    def _get_download_executor(self, channel_id: int, max_workers: int) -> ThreadPoolExecutor:
        normalized_workers = max(1, min(5, max_workers))
        with self.download_executors_lock:
            existing = self.download_executors.get(channel_id)
            if existing is not None and existing[0] == normalized_workers:
                return existing[1]
            if existing is not None:
                existing[1].shutdown(wait=False, cancel_futures=False)
            executor = ThreadPoolExecutor(
                max_workers=normalized_workers,
                thread_name_prefix=f"video-download-{channel_id}",
            )
            self.download_executors[channel_id] = (normalized_workers, executor)
            return executor
