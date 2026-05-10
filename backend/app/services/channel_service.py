from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..models import Channel, ChannelScanType, ChannelStatus, DownloadStatus, Video
from ..schemas import ChannelCreate, ChannelListItem, ChannelUpdate
from .youtube_dl import (
    apply_scan_type_name_suffix,
    infer_channel_name_from_url,
    normalize_channel_url,
)

logger = logging.getLogger("video_transporter.channel")


class ChannelService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_channels(self) -> list[ChannelListItem]:
        rows = self.db.execute(
            select(
                Channel,
                func.count(Video.id).label("video_count"),
                func.sum(case((Video.status == DownloadStatus.COMPLETED, 1), else_=0)).label(
                    "completed_video_count"
                ),
            )
            .outerjoin(Video, Video.channel_id == Channel.id)
            .group_by(Channel.id)
            .order_by(Channel.created_at.desc())
        ).all()

        return [
            ChannelListItem(
                id=channel.id,
                name=channel.name,
                url=channel.url,
                description=channel.description,
                scan_type=channel.scan_type,
                poll_minutes=channel.poll_minutes,
                auto_download=channel.auto_download,
                download_concurrency=channel.download_concurrency,
                initial_download_limit=channel.initial_download_limit,
                preferred_resolution=channel.preferred_resolution,
                prefer_hdr=channel.prefer_hdr,
                status=channel.status,
                last_checked_at=channel.last_checked_at,
                last_sync_at=channel.last_sync_at,
                last_error=channel.last_error,
                created_at=channel.created_at,
                updated_at=channel.updated_at,
                video_count=video_count or 0,
                completed_video_count=completed_video_count or 0,
            )
            for channel, video_count, completed_video_count in rows
        ]

    def get_channel(self, channel_id: int) -> Channel | None:
        return self.db.scalar(
            select(Channel).where(Channel.id == channel_id).options(selectinload(Channel.videos))
        )

    def get_video(self, video_id: int) -> Video | None:
        return self.db.scalar(select(Video).where(Video.id == video_id))

    def list_completed_videos(self) -> list[Video]:
        return list(
            self.db.scalars(select(Video).where(Video.status == DownloadStatus.COMPLETED)).all()
        )

    def list_failed_videos(self, channel_id: int, *, limit: int | None = None) -> list[Video]:
        query = (
            select(Video)
            .where(
                Video.channel_id == channel_id,
                Video.status == DownloadStatus.FAILED,
            )
            .order_by(Video.updated_at.desc(), Video.id.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        return list(
            self.db.scalars(query).all()
        )

    def list_pending_videos(self, channel_id: int, *, limit: int | None = None) -> list[Video]:
        query = (
            select(Video)
            .where(
                Video.channel_id == channel_id,
                Video.status == DownloadStatus.PENDING,
            )
            .order_by(Video.created_at.desc(), Video.id.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        return list(self.db.scalars(query).all())

    def list_deferred_videos(self, channel_id: int, *, limit: int | None = None) -> list[Video]:
        query = (
            select(Video)
            .where(
                Video.channel_id == channel_id,
                Video.status == DownloadStatus.DEFERRED,
            )
            .order_by(Video.created_at.desc(), Video.id.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        return list(self.db.scalars(query).all())

    def create_channel(self, payload: ChannelCreate) -> Channel:
        scan_type = payload.scan_type
        normalized_url = normalize_channel_url(payload.url, scan_type)
        if self._find_channel_by_normalized_url(normalized_url) is not None:
            raise ValueError("Channel URL already exists")
        base_name = (
            payload.name.strip() if payload.name else infer_channel_name_from_url(normalized_url)
        )
        name = apply_scan_type_name_suffix(base_name, scan_type)
        channel = Channel(
            name=name,
            url=normalized_url,
            description=payload.description,
            scan_type=scan_type,
            poll_minutes=payload.poll_minutes,
            auto_download=payload.auto_download,
            download_concurrency=payload.download_concurrency,
            initial_download_limit=payload.initial_download_limit,
            preferred_resolution=payload.preferred_resolution,
            prefer_hdr=payload.prefer_hdr,
            status=payload.status,
        )
        self.db.add(channel)
        self.db.commit()
        self.db.refresh(channel)
        return channel

    def update_channel(self, channel: Channel, payload: ChannelUpdate) -> Channel:
        updates = payload.model_dump(exclude_unset=True)
        next_scan_type = updates.get("scan_type", channel.scan_type)
        if not isinstance(next_scan_type, ChannelScanType):
            next_scan_type = ChannelScanType(next_scan_type)
        next_url = updates.get("url", channel.url)
        next_url = normalize_channel_url(next_url, next_scan_type)
        updates["url"] = next_url
        updates["scan_type"] = next_scan_type
        if next_url and next_url != channel.url:
            existing = self._find_channel_by_normalized_url(next_url, exclude_id=channel.id)
            if existing is not None:
                raise ValueError("Channel URL already exists")
        if "name" in updates:
            updates["name"] = apply_scan_type_name_suffix(updates["name"], next_scan_type)
        elif "scan_type" in updates:
            updates["name"] = apply_scan_type_name_suffix(channel.name, next_scan_type)
        for field, value in updates.items():
            setattr(channel, field, value)
        channel.updated_at = datetime.utcnow()
        self.db.add(channel)
        self.db.commit()
        self.db.refresh(channel)
        return channel

    def delete_channel(self, channel: Channel, *, delete_downloads: bool = False) -> None:
        if delete_downloads:
            self._delete_downloaded_files(channel)
        self.db.delete(channel)
        self.db.commit()

    def _delete_downloaded_files(self, channel: Channel) -> None:
        download_root = settings.download_dir.resolve()
        delete_targets: set[Path] = set()
        channel_dir = download_root / self._safe_dir_name(channel.name)
        if channel_dir.is_dir():
            delete_targets.add(channel_dir.resolve())

        for video in channel.videos:
            for stored_path in (video.download_path, video.subtitle_path):
                if not stored_path:
                    continue
                resolved_path = self._resolve_download_child(stored_path, download_root)
                if resolved_path is None:
                    continue

                if resolved_path.is_file():
                    delete_targets.add(resolved_path.parent)
                elif resolved_path.is_dir():
                    delete_targets.add(resolved_path)

        for target in sorted(delete_targets, key=lambda path: len(path.parts), reverse=True):
            if not self._is_download_child(target, download_root):
                logger.warning(
                    "Skipping channel directory deletion outside download root: %s",
                    target,
                )
                continue
            if target.is_dir():
                shutil.rmtree(target)
            elif target.is_file():
                target.unlink()

    @staticmethod
    def _resolve_download_child(value: str, download_root: Path) -> Path | None:
        resolved_path = Path(value).resolve()
        if ChannelService._is_download_child(resolved_path, download_root):
            return resolved_path
        logger.warning("Skipping channel file deletion outside download directory: %s", value)
        return None

    @staticmethod
    def _is_download_child(path: Path, download_root: Path) -> bool:
        try:
            relative_path = path.resolve().relative_to(download_root)
        except ValueError:
            return False
        return relative_path.parts != ()

    @staticmethod
    def _safe_dir_name(value: str) -> str:
        unsafe_chars = '<>:"/\\|?*'
        safe = "".join("_" if char in unsafe_chars else char for char in value).strip()
        return safe or "channel"

    def _find_channel_by_normalized_url(
        self,
        normalized_url: str,
        *,
        exclude_id: int | None = None,
    ) -> Channel | None:
        for channel in self.db.scalars(select(Channel)).all():
            if exclude_id is not None and channel.id == exclude_id:
                continue
            channel_url = normalize_channel_url(channel.url, channel.scan_type)
            if channel_url == normalized_url:
                return channel
        return None

    def upsert_video(
        self,
        *,
        channel: Channel,
        youtube_video_id: str,
        title: str | None,
        webpage_url: str,
        published_at: datetime | None = None,
        initial_status: DownloadStatus = DownloadStatus.PENDING,
    ) -> tuple[Video, bool]:
        video = self.db.scalar(select(Video).where(Video.youtube_video_id == youtube_video_id))
        created = False
        if video is None:
            video = Video(
                channel_id=channel.id,
                youtube_video_id=youtube_video_id,
                title=title,
                webpage_url=webpage_url,
                published_at=published_at,
                status=initial_status,
            )
            self.db.add(video)
            created = True
        else:
            video.channel_id = channel.id
            video.title = title or video.title
            video.webpage_url = webpage_url
            video.published_at = published_at or video.published_at
        self.db.commit()
        self.db.refresh(video)
        return video, created

    def mark_channel_checked(self, channel: Channel, *, error: str | None = None) -> Channel:
        channel.last_checked_at = datetime.utcnow()
        channel.last_error = error
        self.db.add(channel)
        self.db.commit()
        self.db.refresh(channel)
        return channel

    def mark_channel_synced(self, channel: Channel, *, error: str | None = None) -> Channel:
        now = datetime.utcnow()
        channel.last_checked_at = now
        channel.last_sync_at = now
        channel.last_error = error
        self.db.add(channel)
        self.db.commit()
        self.db.refresh(channel)
        return channel

    def pause_channel(self, channel: Channel, *, error: str | None = None) -> Channel:
        channel.status = ChannelStatus.PAUSED
        channel.last_checked_at = datetime.utcnow()
        channel.last_error = error
        self.db.add(channel)
        self.db.commit()
        self.db.refresh(channel)
        return channel

    def update_video_status(
        self,
        video: Video,
        *,
        status: DownloadStatus,
        download_path: str | None = None,
        subtitle_path: str | None = None,
        error_message: str | None = None,
    ) -> Video:
        video.status = status
        video.download_path = download_path
        video.subtitle_path = subtitle_path
        video.error_message = error_message
        if status == DownloadStatus.COMPLETED:
            video.downloaded_at = datetime.utcnow()
        else:
            video.downloaded_at = None
        self.db.add(video)
        self.db.commit()
        self.db.refresh(video)
        return video

    def update_video_download_path(
        self,
        video: Video,
        download_path: str,
        subtitle_path: str | None = None,
    ) -> Video:
        video.download_path = download_path
        video.subtitle_path = subtitle_path
        video.error_message = None
        self.db.add(video)
        self.db.commit()
        self.db.refresh(video)
        return video

    def mark_interrupted_downloads_failed(self) -> int:
        result = self.db.execute(
            update(Video)
            .where(Video.status == DownloadStatus.DOWNLOADING)
            .values(
                status=DownloadStatus.FAILED,
                download_path=None,
                subtitle_path=None,
                downloaded_at=None,
                error_message="Download was interrupted before the backend shut down or restarted.",
                updated_at=datetime.utcnow(),
            )
        )
        self.db.commit()
        return result.rowcount or 0

    def list_active_channels(self) -> list[Channel]:
        return list(
            self.db.scalars(select(Channel).where(Channel.status == ChannelStatus.ACTIVE)).all()
        )
