from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_serializer

from .models import ChannelStatus, DownloadStatus


class ApiModel(BaseModel):
    model_config = {"from_attributes": True}

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_datetime(self, value: object) -> object:
        if not isinstance(value, datetime):
            return value
        utc_value = (
            value.astimezone(timezone.utc)
            if value.tzinfo is not None
            else value.replace(tzinfo=timezone.utc)
        )
        return utc_value.isoformat().replace("+00:00", "Z")


class ChannelBase(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=500)
    description: str | None = None
    poll_minutes: int = Field(default=30, ge=5, le=1440)
    auto_download: bool = True
    download_concurrency: int = Field(default=1, ge=1, le=5)
    preferred_resolution: int = Field(default=1080, ge=144, le=4320)
    prefer_hdr: bool = False
    status: ChannelStatus = ChannelStatus.ACTIVE


class ChannelCreate(ChannelBase):
    trigger_initial_sync: bool = True


class ChannelUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    poll_minutes: int | None = Field(default=None, ge=5, le=1440)
    auto_download: bool | None = None
    download_concurrency: int | None = Field(default=None, ge=1, le=5)
    preferred_resolution: int | None = Field(default=None, ge=144, le=4320)
    prefer_hdr: bool | None = None
    status: ChannelStatus | None = None


class VideoRead(ApiModel):
    id: int
    youtube_video_id: str
    title: str | None
    webpage_url: str
    status: DownloadStatus
    download_path: str | None
    downloaded_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

class ChannelRead(ApiModel):
    id: int
    name: str
    url: str
    description: str | None
    poll_minutes: int
    auto_download: bool
    download_concurrency: int
    preferred_resolution: int
    prefer_hdr: bool
    status: ChannelStatus
    last_checked_at: datetime | None
    last_sync_at: datetime | None
    next_check_at: datetime | None = None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    videos: list[VideoRead] = []


class ChannelListItem(ApiModel):
    id: int
    name: str
    url: str
    description: str | None
    poll_minutes: int
    auto_download: bool
    download_concurrency: int
    preferred_resolution: int
    prefer_hdr: bool
    status: ChannelStatus
    last_checked_at: datetime | None
    last_sync_at: datetime | None
    next_check_at: datetime | None = None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    video_count: int = 0
    completed_video_count: int = 0


class SyncResponse(ApiModel):
    detail: str


class RetryFailedResponse(ApiModel):
    detail: str
    queued_count: int
    limit: int
