from __future__ import annotations

import json
import logging
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from ..config import BASE_DIR, settings
from ..models import ChannelScanType

logger = logging.getLogger("video_transporter.download")

DownloaderFlavor = Literal["yt-dlp", "youtube-dl"]
SHORTS_NAME_SUFFIX = " - Shorts"
SUBTITLE_EXTENSIONS = {
    ".ass",
    ".json3",
    ".srt",
    ".srv1",
    ".srv2",
    ".srv3",
    ".ssa",
    ".ttml",
    ".vtt",
}


@dataclass(frozen=True)
class DownloadedVideo:
    media_path: Path | None
    subtitle_path: Path | None


def normalize_channel_url(
    channel_url: str,
    scan_type: ChannelScanType = ChannelScanType.VIDEOS,
) -> str:
    parsed = urlparse(channel_url.strip())
    path = parsed.path.rstrip("/")
    tab = scan_type.value
    if not path:
        return channel_url.strip()

    if path.endswith("/videos") or path.endswith("/shorts"):
        base_path = path.rsplit("/", maxsplit=1)[0]
        return parsed._replace(path=f"{base_path}/{tab}").geturl()

    if (
        path.startswith("/@")
        or path.startswith("/channel/")
        or path.startswith("/c/")
        or path.startswith("/user/")
    ):
        return parsed._replace(path=f"{path}/{tab}").geturl()
    return channel_url.strip()


def infer_channel_name_from_url(channel_url: str) -> str:
    parsed = urlparse(channel_url.strip())
    path = parsed.path.strip("/")
    if not path:
        return parsed.netloc or "YouTube Channel"

    parts = [part for part in path.split("/") if part not in {"videos", "shorts"}]
    if not parts:
        return parsed.netloc or "YouTube Channel"
    candidate = parts[-1]
    return candidate.removeprefix("@") or "YouTube Channel"


def apply_scan_type_name_suffix(name: str, scan_type: ChannelScanType) -> str:
    cleaned = name.strip()
    if not cleaned:
        cleaned = "YouTube Channel"

    if scan_type == ChannelScanType.SHORTS:
        if cleaned.lower().endswith(SHORTS_NAME_SUFFIX.lower()):
            return cleaned
        return f"{cleaned}{SHORTS_NAME_SUFFIX}"

    if cleaned.lower().endswith(SHORTS_NAME_SUFFIX.lower()):
        return cleaned[: -len(SHORTS_NAME_SUFFIX)].rstrip()
    return cleaned


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

    def list_channel_videos(
        self,
        channel_url: str,
        scan_type: ChannelScanType = ChannelScanType.VIDEOS,
    ) -> list[dict[str, Any]]:
        normalized_url = normalize_channel_url(channel_url, scan_type)
        logger.info("Listing channel %s: %s", scan_type.value, normalized_url)
        result = self._run(self._build_list_channel_args(normalized_url))
        payload = json.loads(result.stdout or "{}")
        entries = payload.get("entries") or []
        logger.info("Channel scan completed: %s entries discovered", len(entries))
        return [
            {
                "youtube_video_id": entry.get("id"),
                "title": entry.get("title"),
                "webpage_url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                "published_at": self._parse_published_at(entry),
            }
            for entry in entries
            if entry.get("id")
        ]

    def get_channel_metadata(
        self,
        channel_url: str,
        scan_type: ChannelScanType = ChannelScanType.VIDEOS,
    ) -> dict[str, str]:
        normalized_url = normalize_channel_url(channel_url, scan_type)
        logger.info("Resolving channel metadata: %s", normalized_url)
        try:
            result = self._run(self._build_channel_metadata_args(normalized_url))
            payload = json.loads(result.stdout or "{}")
            raw_name = (
                payload.get("channel")
                or payload.get("uploader")
                or payload.get("playlist_uploader")
                or payload.get("title")
                or infer_channel_name_from_url(normalized_url)
            )
        except Exception:
            logger.exception("Channel metadata detection failed, falling back to URL parsing")
            raw_name = infer_channel_name_from_url(normalized_url)
        name = self._clean_detected_channel_name(raw_name)
        return {
            "name": apply_scan_type_name_suffix(name, scan_type),
            "url": normalized_url,
        }

    def download_video(
        self,
        video_url: str,
        channel_name: str,
        *,
        preferred_resolution: int = 1080,
        prefer_hdr: bool = False,
    ) -> DownloadedVideo:
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
        subtitle_path = self.resolve_subtitle_path(channel_name, video_url, final_path)
        if final_path and subtitle_path is None:
            subtitle_path = self.download_subtitles(video_url, channel_name, final_path)
        if final_path:
            logger.info("Video download completed: %s", final_path)
        else:
            logger.info("Video download skipped or no output path returned: %s", video_url)
        if subtitle_path:
            logger.info("Subtitle download completed: %s", subtitle_path)
        return DownloadedVideo(media_path=final_path, subtitle_path=subtitle_path)

    def download_subtitles(
        self,
        video_url: str,
        channel_name: str,
        media_path: str | Path | None = None,
    ) -> Path | None:
        channel_dir = self.download_dir / self._safe_dir_name(channel_name)
        channel_dir.mkdir(parents=True, exist_ok=True)
        output_template = str(channel_dir / "%(upload_date)s [%(id)s].%(ext)s")
        logger.info("Starting subtitle-only download: channel=%s url=%s", channel_name, video_url)
        self._run(self._build_subtitle_download_args(video_url, output_template))
        subtitle_path = self.resolve_subtitle_path(channel_name, video_url, media_path)
        if subtitle_path:
            logger.info("Subtitle-only download completed: %s", subtitle_path)
        else:
            logger.info("Subtitle-only download found no subtitle output: %s", video_url)
        return subtitle_path

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

    def resolve_subtitle_path(
        self,
        channel_name: str,
        video_url_or_id: str,
        media_path: str | Path | None = None,
    ) -> Path | None:
        video_id = self._extract_video_id(video_url_or_id)
        channel_dir = self.download_dir / self._safe_dir_name(channel_name)
        candidates: list[Path] = []

        if channel_dir.is_dir() and video_id:
            candidates.extend(
                path
                for path in channel_dir.iterdir()
                if f"[{video_id}]" in path.name and self._is_subtitle_path(path)
            )

        if media_path is not None:
            media = Path(media_path)
            if media.parent.is_dir():
                candidates.extend(
                    path
                    for path in media.parent.iterdir()
                    if path.name.startswith(f"{media.stem}.") and self._is_subtitle_path(path)
                )

        existing_candidates = [
            path
            for path in candidates
            if path.is_file() and not self._is_temporary_download_path(path)
        ]
        if not existing_candidates:
            return None

        return max(existing_candidates, key=self._subtitle_path_score)

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

    def _build_channel_metadata_args(self, channel_url: str) -> list[str]:
        args = [
            "--ignore-errors",
            "--flat-playlist",
            "--dump-single-json",
            "--playlist-end",
            "1",
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
            args.extend(self._yt_dlp_subtitle_args())
            args.extend(
                self._yt_dlp_download_args(
                    preferred_resolution=preferred_resolution,
                    prefer_hdr=prefer_hdr,
                )
            )
        else:
            args.extend(self._youtube_dl_subtitle_args())
        return [*args, video_url]

    def _build_subtitle_download_args(
        self,
        video_url: str,
        output_template: str,
    ) -> list[str]:
        args = [
            "--ignore-errors",
            "--no-overwrites",
            "--skip-download",
            "-o",
            output_template,
        ]
        if self.downloader_flavor == "yt-dlp":
            args.extend(self._yt_dlp_subtitle_args())
        else:
            args.extend(self._youtube_dl_subtitle_args())
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
    def _yt_dlp_subtitle_args() -> list[str]:
        return [
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "en.*,zh.*,zh-Hans,zh-Hant",
            "--sub-format",
            "vtt/srt/best",
        ]

    @staticmethod
    def _youtube_dl_subtitle_args() -> list[str]:
        return [
            "--write-sub",
            "--write-auto-sub",
            "--sub-lang",
            "en,zh-Hans,zh-Hant,zh-CN,zh-TW,zh",
            "--sub-format",
            "vtt/srt/best",
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
    def _clean_detected_channel_name(name: str) -> str:
        cleaned = name.strip()
        for suffix in (" - Videos", " - Shorts", " Videos", " Shorts"):
            if cleaned.lower().endswith(suffix.lower()):
                return cleaned[: -len(suffix)].rstrip()
        return cleaned

    @staticmethod
    def _parse_published_at(entry: dict[str, Any]) -> datetime | None:
        timestamp = entry.get("timestamp") or entry.get("release_timestamp")
        if isinstance(timestamp, int | float):
            return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)

        upload_date = entry.get("upload_date") or entry.get("release_date")
        if isinstance(upload_date, str):
            for date_format in ("%Y%m%d", "%Y-%m-%d"):
                try:
                    return datetime.strptime(upload_date, date_format)
                except ValueError:
                    continue

        return None

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
    def _is_subtitle_path(path: Path) -> bool:
        return path.suffix.lower() in SUBTITLE_EXTENSIONS

    @staticmethod
    def _download_path_score(path: Path) -> tuple[int, float]:
        is_final_name = 0 if re.search(r"\.f\d+$", path.stem) else 1
        return (is_final_name, path.stat().st_mtime)

    @staticmethod
    def _subtitle_path_score(path: Path) -> tuple[int, int, float]:
        lowered_name = path.name.lower()
        language_score = 0
        for score, token in enumerate((".en.", ".zh.", ".zh-hans.", ".zh-hant."), start=1):
            if token in lowered_name:
                language_score = score
                break
        preferred_formats = {
            ".vtt": 4,
            ".srt": 3,
            ".ttml": 2,
            ".json3": 1,
        }
        format_score = preferred_formats.get(path.suffix.lower(), 0)
        return (language_score, format_score, path.stat().st_mtime)

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
