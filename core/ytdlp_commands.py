"""Stable and Fast yt-dlp command construction without orchestration policy."""

import re
from dataclasses import dataclass
from pathlib import Path

from core.download_contracts import (
    DOWNLOAD_ENGINE_ARIA2_FAST,
    DOWNLOAD_ENGINE_STABLE,
    DownloadError,
    DownloadOptions,
)
from core.runtime_paths import runtime_file


PREMIERE_SAFE_VIDEO_FORMAT = (
    "bv*[height<=1080][ext=mp4][vcodec^=avc1]+ba[ext=m4a][acodec^=mp4a]/"
    "b[height<=1080][ext=mp4][vcodec^=avc1][acodec^=mp4a]"
)
ARIA2_FAST_DOWNLOADER_ARGS = "aria2c:-x 16 -s 16 -j 16 -k 1M"


@dataclass(frozen=True)
class _Aria2RuntimeValidation:
    requested: bool
    available: bool
    path: Path


def _normalize_download_engine(value: object) -> str:
    if value == DOWNLOAD_ENGINE_ARIA2_FAST:
        return DOWNLOAD_ENGINE_ARIA2_FAST
    return DOWNLOAD_ENGINE_STABLE


def _safe_temp_stem(video_id: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", video_id or "video")
    return stem[:64].strip(".") or "video"


def _deno_runtime_path(runtime_file_resolver=runtime_file) -> Path:
    return runtime_file_resolver("deno.exe")


def _aria2_runtime_path(runtime_file_resolver=runtime_file) -> Path:
    return runtime_file_resolver("aria2c.exe")


def _aria2_unavailable_message() -> str:
    return (
        "aria2c.exe is unavailable. Fast download cannot start. "
        "Expected runtime path: data\\bin\\aria2c.exe"
    )


def _base_ytdlp_command(
    options: DownloadOptions,
    *,
    runtime_file_resolver=runtime_file,
) -> list[str]:
    deno_path = _deno_runtime_path(runtime_file_resolver)
    command = [
        str(runtime_file_resolver("yt-dlp.exe")),
        "--no-playlist",
        "--newline",
        "--no-overwrites",
        "--retries",
        "30",
        "--fragment-retries",
        "30",
        "--file-access-retries",
        "10",
        "--socket-timeout",
        "60",
        "--http-chunk-size",
        "1M",
        "--ffmpeg-location",
        str(runtime_file_resolver("ffmpeg.exe").parent),
    ]
    if deno_path.exists():
        command.extend(
            [
                "--js-runtimes",
                f"deno:{deno_path}",
                "--remote-components",
                "ejs:github",
            ]
        )
    if options.speed_limit:
        command.extend(["--limit-rate", options.speed_limit])
    return command


def _base_fast_ytdlp_command(
    options: DownloadOptions,
    aria2_validation: _Aria2RuntimeValidation,
    *,
    runtime_file_resolver=runtime_file,
) -> list[str]:
    if not aria2_validation.available:
        raise DownloadError(_aria2_unavailable_message())

    command = _base_ytdlp_command(
        options,
        runtime_file_resolver=runtime_file_resolver,
    )
    command.extend(
        [
            "--downloader",
            str(aria2_validation.path),
            "--downloader-args",
            ARIA2_FAST_DOWNLOADER_ARGS,
        ]
    )
    return command


def _build_stable_video_ytdlp_command(
    video_id: str,
    temp_dir: Path,
    options: DownloadOptions,
    *,
    runtime_file_resolver=runtime_file,
) -> list[str]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    output_template = str(temp_dir / f"{_safe_temp_stem(video_id)}.%(ext)s")
    return _base_ytdlp_command(
        options,
        runtime_file_resolver=runtime_file_resolver,
    ) + [
        "-N",
        "1",
        "-f",
        PREMIERE_SAFE_VIDEO_FORMAT,
        "--merge-output-format",
        "mp4",
        "--no-write-info-json",
        "--no-write-description",
        "--no-write-thumbnail",
        "-o",
        output_template,
        url,
    ]


def _build_fast_video_ytdlp_command(
    video_id: str,
    temp_dir: Path,
    options: DownloadOptions,
    aria2_validation: _Aria2RuntimeValidation,
    *,
    runtime_file_resolver=runtime_file,
) -> list[str]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    output_template = str(temp_dir / f"{_safe_temp_stem(video_id)}.%(ext)s")
    return _base_fast_ytdlp_command(
        options,
        aria2_validation,
        runtime_file_resolver=runtime_file_resolver,
    ) + [
        "-N",
        "1",
        "-f",
        PREMIERE_SAFE_VIDEO_FORMAT,
        "--merge-output-format",
        "mp4",
        "--no-write-info-json",
        "--no-write-description",
        "--no-write-thumbnail",
        "-o",
        output_template,
        url,
    ]


def _build_video_ytdlp_command(
    video_id: str,
    temp_dir: Path,
    options: DownloadOptions,
    *,
    aria2_validation: _Aria2RuntimeValidation | None = None,
    runtime_file_resolver=runtime_file,
) -> list[str]:
    engine = _normalize_download_engine(options.download_engine)
    if engine == DOWNLOAD_ENGINE_ARIA2_FAST:
        if aria2_validation is None:
            raise DownloadError(_aria2_unavailable_message())
        return _build_fast_video_ytdlp_command(
            video_id,
            temp_dir,
            options,
            aria2_validation,
            runtime_file_resolver=runtime_file_resolver,
        )
    return _build_stable_video_ytdlp_command(
        video_id,
        temp_dir,
        options,
        runtime_file_resolver=runtime_file_resolver,
    )


def _build_stable_audio_ytdlp_command(
    video_id: str,
    temp_dir: Path,
    options: DownloadOptions,
    *,
    runtime_file_resolver=runtime_file,
) -> list[str]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    output_template = str(temp_dir / f"{_safe_temp_stem(video_id)}.%(ext)s")
    return _base_ytdlp_command(
        options,
        runtime_file_resolver=runtime_file_resolver,
    ) + [
        "-N",
        "1",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "--no-write-info-json",
        "--no-write-description",
        "--no-write-thumbnail",
        "-o",
        output_template,
        url,
    ]


def _build_fast_audio_ytdlp_command(
    video_id: str,
    temp_dir: Path,
    options: DownloadOptions,
    aria2_validation: _Aria2RuntimeValidation,
    *,
    runtime_file_resolver=runtime_file,
) -> list[str]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    output_template = str(temp_dir / f"{_safe_temp_stem(video_id)}.%(ext)s")
    return _base_fast_ytdlp_command(
        options,
        aria2_validation,
        runtime_file_resolver=runtime_file_resolver,
    ) + [
        "-N",
        "1",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "--no-write-info-json",
        "--no-write-description",
        "--no-write-thumbnail",
        "-o",
        output_template,
        url,
    ]


def _build_audio_ytdlp_command(
    video_id: str,
    temp_dir: Path,
    options: DownloadOptions,
    *,
    aria2_validation: _Aria2RuntimeValidation | None = None,
    runtime_file_resolver=runtime_file,
) -> list[str]:
    engine = _normalize_download_engine(options.download_engine)
    if engine == DOWNLOAD_ENGINE_ARIA2_FAST:
        if aria2_validation is None:
            raise DownloadError(_aria2_unavailable_message())
        return _build_fast_audio_ytdlp_command(
            video_id,
            temp_dir,
            options,
            aria2_validation,
            runtime_file_resolver=runtime_file_resolver,
        )
    return _build_stable_audio_ytdlp_command(
        video_id,
        temp_dir,
        options,
        runtime_file_resolver=runtime_file_resolver,
    )
