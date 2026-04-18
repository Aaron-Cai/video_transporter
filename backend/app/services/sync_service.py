from __future__ import annotations

import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Channel, DownloadStatus
from .channel_service import ChannelService
from .youtube_dl import YoutubeDlClient

logger = logging.getLogger("video_transporter.sync")


def classify_sync_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return f"下载器缺失：{exc}"

    if isinstance(exc, json.JSONDecodeError):
        return f"输出解析失败：下载器没有返回合法的频道列表 JSON（{exc}）"

    if isinstance(exc, subprocess.CalledProcessError):
        output = "\n".join(
            part.strip()
            for part in (exc.stderr or "", exc.stdout or "")
            if part and part.strip()
        )
        detail = output[:1200] if output else str(exc)
        lowered = output.lower()

        if any(token in lowered for token in ("sign in", "login", "cookie", "cookies", "account")):
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
        elif "http error 429" in lowered or "too many requests" in lowered:
            category = "请求过于频繁"
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

    def submit_video_download(self, channel_id: int, video_id: int) -> None:
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
            executor.submit(self._download_video_by_id, channel_id, video_id)
        finally:
            db.close()

    def shutdown(self) -> None:
        self.sync_executor.shutdown(wait=False, cancel_futures=False)
        with self.download_executors_lock:
            executors = list(self.download_executors.values())
            self.download_executors.clear()
        for _, executor in executors:
            executor.shutdown(wait=False, cancel_futures=False)

    def retry_failed_downloads(self, channel_id: int, *, limit: int = 20) -> int:
        db = SessionLocal()
        try:
            service = ChannelService(db)
            failed_videos = service.list_failed_videos(channel_id, limit=limit)
            for video in failed_videos:
                self.submit_video_download(channel_id, video.id)
            logger.info(
                "Queued failed downloads retry: channel_id=%s count=%s limit=%s",
                channel_id,
                len(failed_videos),
                limit,
            )
            return len(failed_videos)
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
            entries = self.youtube_dl.list_channel_videos(channel.url)
            new_video_count = 0
            for entry in entries:
                video, created = service.upsert_video(channel=channel, **entry)
                if created:
                    new_video_count += 1
                if channel.auto_download and video.status != DownloadStatus.COMPLETED:
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

    def _download_video_by_id(self, channel_id: int, video_id: int) -> None:
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
            self._download_video(db, channel, video_id)
        finally:
            db.close()

    def _download_video(self, db: Session, channel: Channel, video_id: int) -> None:
        service = ChannelService(db)
        channel_id = channel.id
        channel = service.get_channel(channel_id)
        if channel is None:
            logger.warning(
                "Video download skipped because channel vanished: channel_id=%s",
                channel_id,
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
        try:
            logger.info(
                "Starting managed download: channel_id=%s video_id=%s title=%s",
                channel.id,
                video.id,
                video.title,
            )
            service.update_video_status(video, status=DownloadStatus.DOWNLOADING)
            download_path = self.youtube_dl.download_video(
                video.webpage_url,
                channel.name,
                preferred_resolution=channel.preferred_resolution,
                prefer_hdr=channel.prefer_hdr,
            )
            status = DownloadStatus.COMPLETED if download_path else DownloadStatus.SKIPPED
            service.update_video_status(
                video,
                status=status,
                download_path=str(download_path) if download_path else None,
            )
            logger.info(
                "Managed download finished: channel_id=%s video_id=%s status=%s path=%s",
                channel.id,
                video.id,
                status.value,
                download_path,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Video download failed for video_id=%s", video_id)
            service.update_video_status(video, status=DownloadStatus.FAILED, error_message=str(exc))

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
