from __future__ import annotations

import json
import logging
import random
import subprocess
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from ..config import settings


logger = logging.getLogger("video_transporter.download")

DownloaderFlavor = Literal["yt-dlp", "youtube-dl"]


class YoutubeDlClient:
    def __init__(self, preferred_executable: Path, fallback_executable: Path, download_dir: Path) -> None:
        self.preferred_executable = preferred_executable
        self.fallback_executable = fallback_executable
        self.download_dir = download_dir
        self.archive_file = download_dir / "youtube-dl-archive.txt"
        self.cookies_path = settings.youtube_cookies_path
        self.download_interval_min_seconds = settings.download_interval_min_seconds
        self.download_interval_max_seconds = settings.download_interval_max_seconds

    @property
    def executable(self) -> Path:
        if self.preferred_executable.exists():
            return self.preferred_executable
        return self.fallback_executable

    @property
    def downloader_flavor(self) -> DownloaderFlavor:
        executable_name = self.executable.name.lower()
        if "yt-dlp" in executable_name:
            return "yt-dlp"
        return "youtube-dl"

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if not self.executable.exists():
            raise FileNotFoundError(f"Downloader executable not found: {self.executable}")
        full_args = self._with_common_args(args)
        logger.info(
            "Running downloader %s (flavor=%s) with args: %s",
            self.executable.name,
            self.downloader_flavor,
            full_args,
        )
        try:
            return subprocess.run(
                [str(self.executable), *full_args],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.CalledProcessError as exc:
            logger.error("Downloader failed: executable=%s returncode=%s", self.executable.name, exc.returncode)
            if exc.stdout:
                logger.error("Downloader stdout: %s", exc.stdout.strip()[:4000])
            if exc.stderr:
                logger.error("Downloader stderr: %s", exc.stderr.strip()[:4000])
            raise

    def list_channel_videos(self, channel_url: str) -> list[dict[str, Any]]:
        normalized_url = self._normalize_channel_url(channel_url)
        logger.info("Listing channel videos: %s", normalized_url)
        result = self._run(self._build_list_channel_args(normalized_url))
        payload = json.loads(result.stdout or "{}")
        entries = payload.get("entries") or []
        logger.info("Channel scan completed: %s videos discovered", len(entries))
        return [
            {
                "youtube_video_id": entry.get("id"),
                "title": entry.get("title"),
                "webpage_url": f"https://www.youtube.com/watch?v={entry.get('id')}",
            }
            for entry in entries
            if entry.get("id")
        ]

    def download_video(self, video_url: str, channel_name: str) -> Path | None:
        channel_dir = self.download_dir / self._safe_dir_name(channel_name)
        channel_dir.mkdir(parents=True, exist_ok=True)
        output_template = str(channel_dir / "%(upload_date)s [%(id)s].%(ext)s")
        sleep_seconds = self._get_download_interval_seconds()
        if sleep_seconds > 0:
            logger.info("Sleeping %.2f seconds before download to reduce request frequency", sleep_seconds)
            time.sleep(sleep_seconds)
        logger.info("Starting video download: channel=%s url=%s", channel_name, video_url)
        result = self._run(self._build_download_args(video_url, output_template))
        last_path = self._extract_destination_path(result.stdout)
        if last_path:
            logger.info("Video download completed: %s", last_path)
        else:
            logger.info("Video download skipped or no output path returned: %s", video_url)
        return Path(last_path) if last_path else None

    def _with_common_args(self, args: list[str]) -> list[str]:
        common_args: list[str] = []
        if self.cookies_path and self.cookies_path.exists():
            common_args.extend(["--cookies", str(self.cookies_path)])
        return [*common_args, *args]

    def _build_list_channel_args(self, channel_url: str) -> list[str]:
        args = [
            "--ignore-errors",
            "--flat-playlist",
            "--dump-single-json",
        ]
        if self.downloader_flavor == "yt-dlp":
            args.extend(self._yt_dlp_listing_args())
        return [*args, channel_url]

    def _build_download_args(self, video_url: str, output_template: str) -> list[str]:
        args = [
            "--ignore-errors",
            "--no-overwrites",
            "--download-archive",
            str(self.archive_file),
            "-o",
            output_template,
        ]
        if self.downloader_flavor == "yt-dlp":
            args.extend(self._yt_dlp_download_args())
        return [*args, video_url]

    @staticmethod
    def _yt_dlp_listing_args() -> list[str]:
        # Keep yt-dlp-only flags isolated so future changes must explicitly
        # consider whether youtube-dl supports the same behavior.
        return []

    @staticmethod
    def _yt_dlp_download_args() -> list[str]:
        # Keep yt-dlp-only flags isolated so future changes must explicitly
        # consider whether youtube-dl supports the same behavior.
        return []

    @staticmethod
    def _normalize_channel_url(channel_url: str) -> str:
        parsed = urlparse(channel_url)
        path = parsed.path.rstrip("/")
        if not path or path.endswith("/videos"):
            return channel_url
        if path.startswith("/@") or path.startswith("/channel/") or path.startswith("/c/") or path.startswith("/user/"):
            return parsed._replace(path=f"{path}/videos").geturl()
        return channel_url

    @staticmethod
    def _safe_dir_name(value: str) -> str:
        unsafe_chars = '<>:"/\\|?*'
        safe = "".join("_" if char in unsafe_chars else char for char in value).strip()
        return safe or "channel"

    def _get_download_interval_seconds(self) -> float:
        min_seconds = max(0.0, self.download_interval_min_seconds)
        max_seconds = max(min_seconds, self.download_interval_max_seconds)
        if min_seconds == max_seconds:
            return min_seconds
        return random.uniform(min_seconds, max_seconds)

    @staticmethod
    def _extract_destination_path(stdout: str) -> str | None:
        for line in stdout.splitlines():
            if line.startswith("[download] Destination: "):
                return line.removeprefix("[download] Destination: ").strip()
            if line.startswith("[ffmpeg] Merging formats into "):
                parts = line.split('"')
                if len(parts) >= 2:
                    return parts[1]
        return None
