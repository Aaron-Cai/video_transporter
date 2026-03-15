from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.channels import router as channel_router
from .config import settings
from .database import Base, engine
from .logging_config import configure_logging
from .scheduler import ChannelScheduler
from .services.sync_service import SyncManager
from .services.youtube_dl import YoutubeDlClient


configure_logging()

youtube_dl = YoutubeDlClient(settings.yt_dlp_path, settings.youtube_dl_path, settings.download_dir)
sync_manager = SyncManager(youtube_dl)
scheduler = ChannelScheduler(sync_manager)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
        sync_manager.shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

channel_router.sync_manager = sync_manager
channel_router.scheduler = scheduler
app.include_router(channel_router, prefix=settings.api_prefix)

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
