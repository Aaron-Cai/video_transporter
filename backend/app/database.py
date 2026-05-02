from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .config import settings

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()


def ensure_schema() -> None:
    inspector = inspect(engine)
    if "channels" not in inspector.get_table_names():
        return

    channel_columns = {column["name"] for column in inspector.get_columns("channels")}
    with engine.begin() as connection:
        if "download_concurrency" not in channel_columns:
            connection.execute(
                text(
                    "ALTER TABLE channels ADD COLUMN "
                    "download_concurrency INTEGER NOT NULL DEFAULT 1"
                )
            )
        if "initial_download_limit" not in channel_columns:
            connection.execute(
                text(
                    "ALTER TABLE channels ADD COLUMN "
                    "initial_download_limit INTEGER NOT NULL DEFAULT 20"
                )
            )
        if "preferred_resolution" not in channel_columns:
            connection.execute(
                text(
                    "ALTER TABLE channels ADD COLUMN "
                    "preferred_resolution INTEGER NOT NULL DEFAULT 1080"
                )
            )
        if "prefer_hdr" not in channel_columns:
            connection.execute(
                text("ALTER TABLE channels ADD COLUMN prefer_hdr BOOLEAN NOT NULL DEFAULT 0")
            )
        if "scan_type" not in channel_columns:
            connection.execute(
                text(
                    "ALTER TABLE channels ADD COLUMN "
                    "scan_type VARCHAR(20) NOT NULL DEFAULT 'videos'"
                )
            )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
