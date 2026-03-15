from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "Video Transporter"
    api_prefix: str = "/api"
    data_dir: Path = BASE_DIR / "data"
    download_dir: Path = BASE_DIR / "downloads"
    logs_dir: Path = BASE_DIR / "logs"
    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'app.db').as_posix()}"
    yt_dlp_path: Path = BASE_DIR / "bin" / "yt-dlp.exe"
    youtube_dl_path: Path = BASE_DIR / "bin" / "youtube-dl.exe"
    youtube_cookies_path: Path | None = BASE_DIR / "cookies.txt"
    default_poll_minutes: int = 30
    log_backup_days: int = 14
    download_interval_seconds: float = 2.0

    model_config = SettingsConfigDict(
        env_prefix="VIDEO_TRANSPORTER_",
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
