from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class ChannelStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"


class ChannelScanType(str, Enum):
    VIDEOS = "videos"
    SHORTS = "shorts"


class DownloadStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    DEFERRED = "deferred"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class Channel(TimestampMixin, Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scan_type: Mapped[ChannelScanType] = mapped_column(
        SqlEnum(
            ChannelScanType,
            values_callable=lambda enum_class: [item.value for item in enum_class],
        ),
        default=ChannelScanType.VIDEOS,
        nullable=False,
    )
    status: Mapped[ChannelStatus] = mapped_column(
        SqlEnum(ChannelStatus), default=ChannelStatus.ACTIVE, nullable=False
    )
    poll_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_download: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    download_concurrency: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    initial_download_limit: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    preferred_resolution: Mapped[int] = mapped_column(Integer, default=1080, nullable=False)
    prefer_hdr: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    videos: Mapped[list["Video"]] = relationship(
        back_populates="channel",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Video(TimestampMixin, Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    channel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    youtube_video_id: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    webpage_url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[DownloadStatus] = mapped_column(
        SqlEnum(DownloadStatus), default=DownloadStatus.PENDING, nullable=False
    )
    download_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    subtitle_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    channel: Mapped[Channel] = relationship(back_populates="videos")
