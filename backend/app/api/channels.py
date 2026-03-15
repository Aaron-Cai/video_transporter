from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import (
    ChannelCreate,
    ChannelListItem,
    ChannelRead,
    ChannelUpdate,
    RetryFailedResponse,
    SyncResponse,
)
from ..scheduler import ChannelScheduler
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


@router.get("", response_model=list[ChannelListItem])
def list_channels(db: Session = Depends(get_db)) -> list[ChannelListItem]:
    return ChannelService(db).list_channels()


@router.get("/{channel_id}", response_model=ChannelRead)
def get_channel(channel_id: int, db: Session = Depends(get_db)) -> ChannelRead:
    service = ChannelService(db)
    return _get_channel_or_404(service, channel_id)


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
    return channel


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
    return updated


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    scheduler: ChannelScheduler = Depends(get_scheduler),
) -> Response:
    service = ChannelService(db)
    channel = _get_channel_or_404(service, channel_id)
    service.delete_channel(channel)
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
