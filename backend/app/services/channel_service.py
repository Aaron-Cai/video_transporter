from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from ..models import Channel, ChannelStatus, DownloadStatus, Video
from ..schemas import ChannelCreate, ChannelListItem, ChannelUpdate


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
                poll_minutes=channel.poll_minutes,
                auto_download=channel.auto_download,
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

    def create_channel(self, payload: ChannelCreate) -> Channel:
        if self.db.scalar(select(Channel).where(Channel.url == payload.url)) is not None:
            raise ValueError("Channel URL already exists")
        channel = Channel(
            name=payload.name,
            url=payload.url,
            description=payload.description,
            poll_minutes=payload.poll_minutes,
            auto_download=payload.auto_download,
            status=payload.status,
        )
        self.db.add(channel)
        self.db.commit()
        self.db.refresh(channel)
        return channel

    def update_channel(self, channel: Channel, payload: ChannelUpdate) -> Channel:
        updates = payload.model_dump(exclude_unset=True)
        next_url = updates.get("url")
        if next_url and next_url != channel.url:
            existing = self.db.scalar(select(Channel).where(Channel.url == next_url))
            if existing is not None:
                raise ValueError("Channel URL already exists")
        for field, value in updates.items():
            setattr(channel, field, value)
        channel.updated_at = datetime.utcnow()
        self.db.add(channel)
        self.db.commit()
        self.db.refresh(channel)
        return channel

    def delete_channel(self, channel: Channel) -> None:
        self.db.delete(channel)
        self.db.commit()

    def upsert_video(
        self,
        *,
        channel: Channel,
        youtube_video_id: str,
        title: str | None,
        webpage_url: str,
    ) -> tuple[Video, bool]:
        video = self.db.scalar(select(Video).where(Video.youtube_video_id == youtube_video_id))
        created = False
        if video is None:
            video = Video(
                channel_id=channel.id,
                youtube_video_id=youtube_video_id,
                title=title,
                webpage_url=webpage_url,
            )
            self.db.add(video)
            created = True
        else:
            video.channel_id = channel.id
            video.title = title or video.title
            video.webpage_url = webpage_url
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

    def update_video_status(
        self,
        video: Video,
        *,
        status: DownloadStatus,
        download_path: str | None = None,
        error_message: str | None = None,
    ) -> Video:
        video.status = status
        video.download_path = download_path
        video.error_message = error_message
        if status == DownloadStatus.COMPLETED:
            video.downloaded_at = datetime.utcnow()
        else:
            video.downloaded_at = None
        self.db.add(video)
        self.db.commit()
        self.db.refresh(video)
        return video

    def list_active_channels(self) -> list[Channel]:
        return list(self.db.scalars(select(Channel).where(Channel.status == ChannelStatus.ACTIVE)).all())
