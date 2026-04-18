from __future__ import annotations

import json
import logging
import random
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from ..config import BASE_DIR, settings

logger = logging.getLogger("video_transporter.download")

DownloaderFlavor = Literal["yt-dlp", "youtube-dl"]


class YoutubeDlClient:
    def __init__(
        self,
        preferred_executable: Path,
        fallback_executable: Path,
        download_dir: Path,
    ) -> None:
        self.preferred_executable = preferred_executable
        self.fallback_executable = fallback_executable
        self.download_dir = download_dir
        self.archive_file = download_dir / "youtube-dl-archive.txt"
        self.cookies_path = settings.youtube_cookies_path
        self.download_interval_min_seconds = settings.download_interval_min_seconds
        self.download_interval_max_seconds = settings.download_interval_max_seconds
        self.ffmpeg_executable = self._detect_ffmpeg_executable()

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
            logger.error(
                "Downloader failed: executable=%s returncode=%s",
                self.executable.name,
                exc.returncode,
            )
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

    def download_video(
        self,
        video_url: str,
        channel_name: str,
        *,
        preferred_resolution: int = 1080,
        prefer_hdr: bool = False,
    ) -> Path | None:
        channel_dir = self.download_dir / self._safe_dir_name(channel_name)
        channel_dir.mkdir(parents=True, exist_ok=True)
        output_template = str(channel_dir / "%(upload_date)s [%(id)s].%(ext)s")
        sleep_seconds = self._get_download_interval_seconds()
        if sleep_seconds > 0:
            logger.info(
                "Sleeping %.2f seconds before download to reduce request frequency",
                sleep_seconds,
            )
            time.sleep(sleep_seconds)
        logger.info("Starting video download: channel=%s url=%s", channel_name, video_url)
        result = self._run(
            self._build_download_args(
                video_url,
                output_template,
                preferred_resolution=preferred_resolution,
                prefer_hdr=prefer_hdr,
            )
        )
        reported_path = self._extract_destination_path(result.stdout)
        final_path = self.resolve_download_path(channel_name, video_url, reported_path)
        if final_path:
            logger.info("Video download completed: %s", final_path)
        else:
            logger.info("Video download skipped or no output path returned: %s", video_url)
        return final_path

    def resolve_download_path(
        self,
        channel_name: str,
        video_url_or_id: str,
        reported_path: str | Path | None = None,
    ) -> Path | None:
        video_id = self._extract_video_id(video_url_or_id)
        channel_dir = self.download_dir / self._safe_dir_name(channel_name)
        reported = Path(reported_path) if reported_path else None
        candidates: list[Path] = []

        if channel_dir.is_dir() and video_id:
            candidates.extend(
                path for path in channel_dir.iterdir() if f"[{video_id}]" in path.name
            )
        if reported is not None:
            candidates.append(reported)

        existing_candidates = [
            path
            for path in candidates
            if path.is_file() and not self._is_temporary_download_path(path)
        ]
        if not existing_candidates:
            return None

        return max(existing_candidates, key=self._download_path_score)

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

    def _build_download_args(
        self,
        video_url: str,
        output_template: str,
        *,
        preferred_resolution: int,
        prefer_hdr: bool,
    ) -> list[str]:
        args = [
            "--ignore-errors",
            "--no-overwrites",
            "--download-archive",
            str(self.archive_file),
            "-o",
            output_template,
        ]
        if self.downloader_flavor == "yt-dlp":
            args.extend(
                self._yt_dlp_download_args(
                    preferred_resolution=preferred_resolution,
                    prefer_hdr=prefer_hdr,
                )
            )
        return [*args, video_url]

    @staticmethod
    def _yt_dlp_listing_args() -> list[str]:
        # Keep yt-dlp-only flags isolated so future changes must explicitly
        # consider whether youtube-dl supports the same behavior.
        return []

    def _yt_dlp_download_args(self, *, preferred_resolution: int, prefer_hdr: bool) -> list[str]:
        # Keep yt-dlp-only flags isolated so future changes must explicitly
        # consider whether youtube-dl supports the same behavior.
        normalized_resolution = max(144, preferred_resolution)
        hdr_filter = "" if prefer_hdr else '[dynamic_range="SDR"]'
        sort_order = "res,hdr" if prefer_hdr else "res,+hdr"
        if self.ffmpeg_executable is not None:
            preferred_format = (
                f'bestvideo*{hdr_filter}[height<={normalized_resolution}]+bestaudio/'
                f'best{hdr_filter}[height<={normalized_resolution}]'
            )
            fallback_format = f"bestvideo*{hdr_filter}+bestaudio/best{hdr_filter}"
            return [
                "--ffmpeg-location",
                str(self.ffmpeg_executable.parent),
                "-f",
                f"{preferred_format}/{fallback_format}",
                "-S",
                sort_order,
            ]

        preferred_format = f"best{hdr_filter}[height<={normalized_resolution}]"
        fallback_format = f"best{hdr_filter}"
        return [
            "-f",
            f"{preferred_format}/{fallback_format}",
            "-S",
            sort_order,
        ]

    @staticmethod
    def _detect_ffmpeg_executable() -> Path | None:
        candidates = [
            BASE_DIR / "bin" / "ffmpeg.exe",
            BASE_DIR / "bin" / "ffmpeg",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        system_ffmpeg = shutil.which("ffmpeg")
        return Path(system_ffmpeg) if system_ffmpeg else None

    @staticmethod
    def _normalize_channel_url(channel_url: str) -> str:
        parsed = urlparse(channel_url)
        path = parsed.path.rstrip("/")
        if not path or path.endswith("/videos"):
            return channel_url
        if (
            path.startswith("/@")
            or path.startswith("/channel/")
            or path.startswith("/c/")
            or path.startswith("/user/")
        ):
            return parsed._replace(path=f"{path}/videos").geturl()
        return channel_url

    @staticmethod
    def _safe_dir_name(value: str) -> str:
        unsafe_chars = '<>:"/\\|?*'
        safe = "".join("_" if char in unsafe_chars else char for char in value).strip()
        return safe or "channel"

    @staticmethod
    def _extract_video_id(video_url_or_id: str) -> str:
        parsed = urlparse(video_url_or_id)
        if parsed.query:
            video_id = parse_qs(parsed.query).get("v", [None])[0]
            if video_id:
                return video_id
        path = parsed.path.strip("/")
        if path:
            return path.rsplit("/", maxsplit=1)[-1]
        return video_url_or_id

    @staticmethod
    def _is_temporary_download_path(path: Path) -> bool:
        lowered_name = path.name.lower()
        return any(
            token in lowered_name
            for token in (
                ".part",
                ".ytdl",
                ".temp",
                ".tmp",
            )
        )

    @staticmethod
    def _download_path_score(path: Path) -> tuple[int, float]:
        is_final_name = 0 if re.search(r"\.f\d+$", path.stem) else 1
        return (is_final_name, path.stat().st_mtime)

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
