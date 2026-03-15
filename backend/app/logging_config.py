from __future__ import annotations

from logging.config import dictConfig

from .config import settings


def configure_logging() -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    app_log = settings.logs_dir / "app.log"
    download_log = settings.logs_dir / "download.log"

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                },
                "app_file": {
                    "class": "logging.handlers.TimedRotatingFileHandler",
                    "formatter": "standard",
                    "filename": str(app_log),
                    "when": "midnight",
                    "backupCount": settings.log_backup_days,
                    "encoding": "utf-8",
                },
                "download_file": {
                    "class": "logging.handlers.TimedRotatingFileHandler",
                    "formatter": "standard",
                    "filename": str(download_log),
                    "when": "midnight",
                    "backupCount": settings.log_backup_days,
                    "encoding": "utf-8",
                },
            },
            "root": {
                "level": "INFO",
                "handlers": ["console", "app_file"],
            },
            "loggers": {
                "video_transporter": {
                    "level": "INFO",
                    "handlers": ["console", "app_file"],
                    "propagate": False,
                },
                "video_transporter.download": {
                    "level": "INFO",
                    "handlers": ["console", "download_file"],
                    "propagate": False,
                },
                "uvicorn": {
                    "level": "INFO",
                    "handlers": ["console", "app_file"],
                    "propagate": False,
                },
                "uvicorn.error": {
                    "level": "INFO",
                    "handlers": ["console", "app_file"],
                    "propagate": False,
                },
                "uvicorn.access": {
                    "level": "INFO",
                    "handlers": ["console", "app_file"],
                    "propagate": False,
                },
            },
        }
    )
