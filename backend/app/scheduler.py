from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .database import SessionLocal
from .services.channel_service import ChannelService
from .services.sync_service import SyncManager

logger = logging.getLogger("video_transporter.scheduler")


class ChannelScheduler:
    def __init__(self, sync_manager: SyncManager) -> None:
        self.sync_manager = sync_manager
        self.scheduler = BackgroundScheduler()

    def start(self) -> None:
        self.scheduler.start()
        logger.info("Scheduler started")
        self.reload_jobs()

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    def get_next_check_at(self, channel_id: int) -> datetime | None:
        job = self.scheduler.get_job(f"channel-sync-{channel_id}")
        return job.next_run_time if job is not None else None

    def reload_jobs(self) -> None:
        self.scheduler.remove_all_jobs()
        logger.info("Reloading scheduler jobs")
        db = SessionLocal()
        try:
            for channel in ChannelService(db).list_active_channels():
                self.scheduler.add_job(
                    self.sync_manager.submit_sync,
                    trigger=IntervalTrigger(minutes=channel.poll_minutes),
                    args=[channel.id],
                    id=f"channel-sync-{channel.id}",
                    max_instances=1,
                    replace_existing=True,
                )
                logger.info(
                    "Scheduled channel sync: channel_id=%s interval_minutes=%s",
                    channel.id,
                    channel.poll_minutes,
                )
        finally:
            db.close()
