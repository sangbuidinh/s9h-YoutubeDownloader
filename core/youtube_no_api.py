import json
import re
import subprocess
import urllib.parse
from datetime import datetime, timezone

from core.runtime_paths import runtime_file
from core.youtube_api import ChannelInfo, SHORT_VIDEO_DEFAULT_THRESHOLD_SECONDS, VideoItem, is_short_video


NO_API_PAGE_SIZE = 100


class YoutubeNoApiError(Exception):
    pass


def fetch_latest_video_page_no_api(
    channel_input: str,
    progress=None,
    cookies_path: str = "",
    min_visible_duration_seconds: int = SHORT_VIDEO_DEFAULT_THRESHOLD_SECONDS,
) -> tuple[ChannelInfo, list[VideoItem], str]:
    _progress(progress, "[INFO] Fetching latest uploads with yt-dlp flat listing...")
    output = run_no_api_listing_command(
        build_no_api_listing_command(
            channel_input,
            cookies_path=cookies_path,
            playlist_start=1,
            playlist_end=NO_API_PAGE_SIZE,
        )
    )
    metadata, records = parse_no_api_listing_payload(output)
    videos = _video_items_from_records(records, start_order=1)
    if not videos:
        raise YoutubeNoApiError("No videos found or cannot resolve channel")
    channel = _channel_info_from_metadata_and_records(metadata, records, channel_input)
    input_channel_name = _channel_name_from_input(channel_input)
    if not _channel_name_from_metadata(metadata, records) and input_channel_name:
        _progress(
            progress,
            f"[WARNING] Could not resolve channel name from yt-dlp metadata; using input-derived name: {input_channel_name}",
        )
    next_page_token = _next_page_token(1, len(videos))
    return channel, videos, next_page_token


def fetch_more_videos_no_api(
    channel_input: str,
    page_token: str,
    start_order: int,
    progress=None,
    cookies_path: str = "",
    min_visible_duration_seconds: int = SHORT_VIDEO_DEFAULT_THRESHOLD_SECONDS,
) -> tuple[list[VideoItem], str]:
    if not page_token:
        _progress(progress, "[INFO] No more videos.")
        return [], ""
    try:
        playlist_start = int(str(page_token).strip())
    except ValueError:
        raise YoutubeNoApiError("Cannot resolve channel")
    if playlist_start < 1:
        raise YoutubeNoApiError("Cannot resolve channel")

    playlist_end = playlist_start + NO_API_PAGE_SIZE - 1
    _progress(progress, f"[INFO] Loading videos {playlist_start}-{playlist_end} with yt-dlp flat listing...")
    output = run_no_api_listing_command(
        build_no_api_listing_command(
            channel_input,
            cookies_path=cookies_path,
            playlist_start=playlist_start,
            playlist_end=playlist_end,
        )
    )
    _metadata, records = parse_no_api_listing_payload(output)
    videos = _video_items_from_records(records, start_order=start_order)
    next_page_token = _next_page_token(playlist_start, len(videos))
    return videos, next_page_token


def build_no_api_listing_command(
    channel_input: str,
    cookies_path: str = "",
    playlist_start: int = 1,
    playlist_end: int = NO_API_PAGE_SIZE,
) -> list[str]:
    command = [
        str(runtime_file("yt-dlp.exe")),
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-start",
        str(max(1, int(playlist_start))),
        "--playlist-end",
        str(max(1, int(playlist_end))),
        "--no-warnings",
    ]
    if cookies_path:
        command.extend(["--cookies", cookies_path])
    command.append(channel_input)
    return command


def run_no_api_listing_command(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        raise YoutubeNoApiError("yt-dlp.exe missing")
    except subprocess.TimeoutExpired:
        raise YoutubeNoApiError("Network error")
    except OSError as exc:
        raise YoutubeNoApiError(str(exc) or "Network error")

    if result.returncode != 0:
        combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        raise YoutubeNoApiError(classify_no_api_error(combined_output))
    return result.stdout or ""


def parse_no_api_video_line(line: str, display_order: int) -> VideoItem | None:
    try:
        data = json.loads(line)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return _video_item_from_record(data, display_order)


def parse_no_api_listing_output(
    output: str,
    min_visible_duration_seconds: int = SHORT_VIDEO_DEFAULT_THRESHOLD_SECONDS,
    start_order: int = 1,
) -> list[VideoItem]:
    _metadata, records = parse_no_api_listing_payload(output)
    return _video_items_from_records(records, start_order=start_order)


def parse_no_api_listing_payload(output: str) -> tuple[dict, list[dict]]:
    try:
        data = json.loads(output or "")
    except (TypeError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict) and "entries" in data:
        entries = data.get("entries")
        records = [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []
        return data, records
    return {}, parse_no_api_json_lines(output)


def parse_no_api_json_lines(output: str) -> list[dict]:
    records: list[dict] = []
    for line in (output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            records.append(data)
    return records


def is_no_api_short_video(
    video: VideoItem,
    threshold_seconds: int = SHORT_VIDEO_DEFAULT_THRESHOLD_SECONDS,
) -> bool:
    duration_seconds = int(getattr(video, "duration_seconds", 0) or 0)
    return duration_seconds > 0 and is_short_video(video, threshold_seconds)


def classify_no_api_error(output: str) -> str:
    lower = (output or "").lower()
    if (
        "sign in to confirm" in lower
        or "not a bot" in lower
        or "confirm you're not a bot" in lower
        or "use --cookies" in lower
        or ("cookies" in lower and "youtube" in lower)
    ):
        return "Sign in to confirm YouTube access; use --cookies by enabling Cookies and selecting a valid cookies file."
    if "unsupported url" in lower or "no suitable extractor" in lower:
        return "Cannot resolve channel"
    if "timeout" in lower or "timed out" in lower or "network" in lower or "unable to download webpage" in lower:
        return "Network error"
    if "cookies" in lower and ("file not found" in lower or "no such file" in lower or "could not open" in lower):
        return "Cookies file missing"
    if "yt-dlp.exe missing" in lower or ("file not found" in lower and "yt-dlp" in lower):
        return "yt-dlp.exe missing"
    if not (output or "").strip():
        return "No videos found or cannot resolve channel"
    tail = _short_error_tail(output)
    return tail or "No videos found or cannot resolve channel"


def sanitize_no_api_log_text(text: str) -> str:
    return re.sub(r"(--cookies(?:=|\s+))(\"[^\"]+\"|'[^']+'|\S+)", r"\1***", text or "")


def _video_items_from_records(records: list[dict], start_order: int = 1) -> list[VideoItem]:
    videos: list[VideoItem] = []
    for record in records:
        video = _video_item_from_record(record, start_order + len(videos))
        if video is not None:
            videos.append(video)
    return videos


def _video_item_from_record(data: dict, display_order: int) -> VideoItem | None:
    video_id = str(data.get("id") or "").strip()
    if not video_id:
        return None
    duration_seconds = _duration_seconds(data.get("duration"))
    return VideoItem(
        video_id=video_id,
        title=str(data.get("title") or "(Untitled)").strip() or "(Untitled)",
        duration=_duration_text(duration_seconds) if duration_seconds > 0 else "",
        published_at=_published_date(data),
        thumbnail_url=_best_thumbnail_url(data),
        display_order=display_order,
        duration_seconds=duration_seconds,
    )


def _channel_info_from_metadata_and_records(
    metadata: dict,
    records: list[dict],
    channel_input: str,
) -> ChannelInfo:
    channel_id = _first_text(
        metadata.get("channel_id"),
        metadata.get("uploader_id"),
        _normalize_channel_url(str(metadata.get("channel_url") or "")),
    )
    metadata_id = _text(metadata.get("id"))
    if not channel_id and _looks_like_channel_id(metadata_id):
        channel_id = metadata_id
    if not channel_id:
        channel_id = _first_record_text(records, "channel_id")
    if not channel_id:
        channel_id = _first_record_text(records, "uploader_id")
    if not channel_id:
        channel_id = _first_record_channel_url(records)
    if not channel_id:
        channel_id = _normalize_channel_url(channel_input)
    if not channel_id:
        channel_id = channel_input.strip()

    channel_name = _channel_name_from_metadata(metadata, records)
    if not channel_name:
        channel_name = _channel_name_from_input(channel_input)
    if not channel_name:
        channel_name = channel_id
    if not channel_name:
        channel_name = "Channel"

    # In None mode uploads_playlist_id stores the original yt-dlp source input,
    # not a real YouTube uploads playlist id. It is used for Load more.
    return ChannelInfo(
        channel_id=channel_id,
        channel_name=channel_name,
        uploads_playlist_id=channel_input,
    )


def _channel_name_from_metadata(metadata: dict, records: list[dict]) -> str:
    channel_name = _first_text(
        metadata.get("channel"),
        metadata.get("uploader"),
        metadata.get("playlist_uploader"),
        metadata.get("playlist_channel"),
    )
    if channel_name:
        return channel_name

    title = _text(metadata.get("title"))
    if title and not _is_generic_channel_title(title):
        return title

    playlist_title = _text(metadata.get("playlist_title"))
    if playlist_title:
        return playlist_title

    channel_name = _first_record_text(records, "channel")
    if channel_name:
        return channel_name
    return _first_record_text(records, "uploader")


def _channel_name_from_input(channel_input: str) -> str:
    raw = (channel_input or "").strip()
    if not raw:
        return ""
    if raw.startswith("@"):
        return _clean_channel_name_candidate(raw[1:])
    if _looks_like_channel_id(raw):
        return raw

    parse_target = raw
    if "youtube.com/" in raw.lower() and "://" not in raw:
        parse_target = f"https://{raw}"
    parsed = urllib.parse.urlparse(parse_target)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if segments:
        first = urllib.parse.unquote(segments[0])
        if first.startswith("@"):
            return _clean_channel_name_candidate(first[1:])
        if first in {"channel", "c", "user"} and len(segments) > 1:
            return _clean_channel_name_candidate(segments[1])
        if parsed.netloc.lower().endswith("youtube.com"):
            return _clean_channel_name_candidate(first)

    if "/" not in raw and "\\" not in raw and "://" not in raw:
        return _clean_channel_name_candidate(raw)
    return ""


def _first_record_text(records: list[dict], key: str) -> str:
    for record in records:
        value = _text(record.get(key))
        if value:
            return value
    return ""


def _first_record_channel_url(records: list[dict]) -> str:
    for record in records:
        value = _normalize_channel_url(str(record.get("channel_url") or ""))
        if value:
            return value
    return ""


def _first_text(*values) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _text(value) -> str:
    return str(value or "").strip()


def _looks_like_channel_id(value: str) -> bool:
    return bool((value or "").strip().startswith("UC"))


def _is_generic_channel_title(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", (value or "").strip()).casefold()
    return normalized in {"channel", "videos", "uploads", "youtube", "playlist"}


def _clean_channel_name_candidate(value: str) -> str:
    text = urllib.parse.unquote(value or "")
    text = text.strip().strip("/")
    text = text.split("?", 1)[0].split("#", 1)[0].strip()
    if text.startswith("@"):
        text = text[1:].strip()
    if not text or text.casefold() in {"videos", "shorts", "streams", "featured", "playlists", "community"}:
        return ""
    return text


def _duration_seconds(value) -> int:
    if value is None or value == "":
        return 0
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, seconds)


def _duration_text(seconds_total: int) -> str:
    hours = seconds_total // 3600
    minutes = (seconds_total % 3600) // 60
    seconds = seconds_total % 60
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _published_date(data: dict) -> str:
    upload_date = str(data.get("upload_date") or "").strip()
    if re.fullmatch(r"\d{8}", upload_date):
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", upload_date):
        return upload_date

    timestamp = data.get("timestamp")
    try:
        if timestamp is not None:
            return datetime.fromtimestamp(int(timestamp), timezone.utc).date().isoformat()
    except (OSError, OverflowError, TypeError, ValueError):
        pass
    return ""


def _best_thumbnail_url(data: dict) -> str:
    thumbnails = data.get("thumbnails")
    if isinstance(thumbnails, list):
        candidates = [item for item in thumbnails if isinstance(item, dict) and item.get("url")]
        if candidates:
            best = max(candidates, key=_thumbnail_score)
            return str(best.get("url") or "")
    if isinstance(thumbnails, dict):
        for key in ("maxres", "standard", "high", "medium", "default"):
            item = thumbnails.get(key)
            if isinstance(item, dict) and item.get("url"):
                return str(item.get("url") or "")
    thumbnail = data.get("thumbnail")
    return str(thumbnail or "")


def _thumbnail_score(item: dict) -> tuple[int, int]:
    try:
        preference = int(item.get("preference") or 0)
    except (TypeError, ValueError):
        preference = 0
    try:
        area = int(item.get("width") or 0) * int(item.get("height") or 0)
    except (TypeError, ValueError):
        area = 0
    return preference, area


def _normalize_channel_url(channel_url: str) -> str:
    return (channel_url or "").strip().rstrip("/")


def _next_page_token(playlist_start: int, result_count: int) -> str:
    if result_count >= NO_API_PAGE_SIZE:
        return str(playlist_start + NO_API_PAGE_SIZE)
    return ""


def _short_error_tail(output: str, limit: int = 260) -> str:
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if not lines:
        return ""
    text = " ".join(lines[-3:])
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _progress(progress, message: str) -> None:
    if progress:
        progress(sanitize_no_api_log_text(message))
