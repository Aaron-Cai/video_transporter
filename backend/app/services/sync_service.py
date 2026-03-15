from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Channel, DownloadStatus
from .channel_service import ChannelService
from .youtube_dl import YoutubeDlClient


logger = logging.getLogger("video_transporter.sync")


class SyncManager:
    def __init__(self, youtube_dl: YoutubeDlClient) -> None:
        self.youtube_dl = youtube_dl
        self.sync_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="channel-sync")
        self.download_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="video-download")

    def submit_sync(self, channel_id: int) -> None:
        logger.info("Queueing channel sync: channel_id=%s", channel_id)
        self.sync_executor.submit(self._sync_channel, channel_id)

    def submit_video_download(self, channel_id: int, video_id: int) -> None:
        logger.info("Queueing video download: channel_id=%s video_id=%s", channel_id, video_id)
        self.download_executor.submit(self._download_video_by_id, channel_id, video_id)

    def shutdown(self) -> None:
        self.sync_executor.shutdown(wait=False, cancel_futures=False)
        self.download_executor.shutdown(wait=False, cancel_futures=False)

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
                logger.warning("Channel sync skipped because channel does not exist: channel_id=%s", channel_id)
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
                ChannelService(db).mark_channel_checked(channel, error=str(exc))
        finally:
            db.close()

    def _download_video_by_id(self, channel_id: int, video_id: int) -> None:
        db = SessionLocal()
        try:
            channel = ChannelService(db).get_channel(channel_id)
            if channel is None:
                logger.warning(
                    "Video download skipped because channel does not exist: channel_id=%s video_id=%s",
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
            logger.warning("Video download skipped because channel vanished: channel_id=%s", channel_id)
            return
        video = service.get_video(video_id)
        if video is None:
            logger.warning("Video download skipped because video does not exist: channel_id=%s video_id=%s", channel.id, video_id)
            return
        if video.status == DownloadStatus.COMPLETED:
            logger.info("Video download skipped because already completed: channel_id=%s video_id=%s", channel.id, video_id)
            return
        try:
            logger.info(
                "Starting managed download: channel_id=%s video_id=%s title=%s",
                channel.id,
                video.id,
                video.title,
            )
            service.update_video_status(video, status=DownloadStatus.DOWNLOADING)
            download_path = self.youtube_dl.download_video(video.webpage_url, channel.name)
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
