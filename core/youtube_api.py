import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from core.runtime_paths import runtime_file

API_BASE = "https://www.googleapis.com/youtube/v3"
USER_AGENT = "YouTube Downloader Source/1.0"
DURATION_FILTER_DEFAULT_HIDE_BELOW_SECONDS = 180
DURATION_FILTER_DEFAULT_HIDE_ABOVE_SECONDS = 3600
VISIBLE_VIDEO_FETCH_TARGET = 100
MAX_UPLOADS_SCAN_LIMIT = 500
_DURATION_PATTERN = re.compile(
    r"^P"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?"
    r")?"
    r"$"
)


@dataclass
class ChannelInfo:
    channel_id: str
    channel_name: str
    uploads_playlist_id: str


@dataclass
class VideoItem:
    video_id: str
    title: str
    duration: str
    published_at: str
    thumbnail_url: str
    display_order: int
    duration_seconds: int | None = None
    live_broadcast_content: str = "none"
    sanitized_filename_base: str = ""
    status: str = "Chưa tải"


class YoutubeApiError(Exception):
    def __init__(self, code: str, message: str, retry_next_key: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_next_key = retry_next_key


def mask_api_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


def sanitize_log_text(text: str) -> str:
    return re.sub(r"([?&]key=)[^&\s]+", r"\1***", text or "")


def read_api_keys(manual_key: str = "", api_key_file: Path | None = None) -> list[str]:
    keys: list[str] = []
    if manual_key and manual_key.strip():
        keys.append(manual_key.strip())

    api_key_file = api_key_file or runtime_file("api key.txt")
    try:
        for line in api_key_file.read_text(encoding="utf-8").splitlines():
            key = line.strip()
            if key:
                keys.append(key)
    except FileNotFoundError:
        pass

    deduped: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def fetch_latest_videos(
    channel_input: str,
    manual_api_key: str = "",
    progress=None,
    api_key_file: Path | None = None,
    hide_below_duration_enabled: bool = True,
    min_visible_duration_seconds: int = DURATION_FILTER_DEFAULT_HIDE_BELOW_SECONDS,
    hide_above_duration_enabled: bool = True,
    max_visible_duration_seconds: int = DURATION_FILTER_DEFAULT_HIDE_ABOVE_SECONDS,
) -> tuple[ChannelInfo, list[VideoItem]]:
    channel, videos, _next_page_token = fetch_latest_video_page(
        channel_input,
        manual_api_key,
        progress,
        api_key_file,
        hide_below_duration_enabled=hide_below_duration_enabled,
        min_visible_duration_seconds=min_visible_duration_seconds,
        hide_above_duration_enabled=hide_above_duration_enabled,
        max_visible_duration_seconds=max_visible_duration_seconds,
    )
    return channel, videos


def fetch_latest_video_page(
    channel_input: str,
    manual_api_key: str = "",
    progress=None,
    api_key_file: Path | None = None,
    hide_below_duration_enabled: bool = True,
    min_visible_duration_seconds: int = DURATION_FILTER_DEFAULT_HIDE_BELOW_SECONDS,
    hide_above_duration_enabled: bool = True,
    max_visible_duration_seconds: int = DURATION_FILTER_DEFAULT_HIDE_ABOVE_SECONDS,
) -> tuple[ChannelInfo, list[VideoItem], str]:
    keys = read_api_keys(manual_api_key, api_key_file)
    if not keys:
        raise YoutubeApiError("invalid_key", "Invalid API key")

    last_key_error: YoutubeApiError | None = None
    for index, api_key in enumerate(keys):
        try:
            return _fetch_latest_videos_with_key(
                channel_input,
                api_key,
                progress,
                hide_below_duration_enabled,
                min_visible_duration_seconds,
                hide_above_duration_enabled,
                max_visible_duration_seconds,
            )
        except YoutubeApiError as exc:
            if exc.retry_next_key:
                last_key_error = exc
                if index < len(keys) - 1:
                    _progress(
                        progress,
                        f"[WARNING] API key {mask_api_key(api_key)} failed: {exc.message}. Trying next key...",
                    )
                    continue
            raise exc

    raise last_key_error or YoutubeApiError("invalid_key", "Invalid API key")


def fetch_more_videos(
    uploads_playlist_id: str,
    page_token: str,
    start_order: int,
    manual_api_key: str = "",
    progress=None,
    api_key_file: Path | None = None,
    hide_below_duration_enabled: bool = True,
    min_visible_duration_seconds: int = DURATION_FILTER_DEFAULT_HIDE_BELOW_SECONDS,
    hide_above_duration_enabled: bool = True,
    max_visible_duration_seconds: int = DURATION_FILTER_DEFAULT_HIDE_ABOVE_SECONDS,
) -> tuple[list[VideoItem], str]:
    if not page_token:
        _progress(progress, "[INFO] No more videos.")
        return [], ""

    keys = read_api_keys(manual_api_key, api_key_file)
    if not keys:
        raise YoutubeApiError("invalid_key", "Invalid API key")

    last_key_error: YoutubeApiError | None = None
    for index, api_key in enumerate(keys):
        try:
            return _fetch_more_videos_with_key(
                uploads_playlist_id,
                page_token,
                start_order,
                api_key,
                hide_below_duration_enabled,
                min_visible_duration_seconds,
                hide_above_duration_enabled,
                max_visible_duration_seconds,
            )
        except YoutubeApiError as exc:
            if exc.retry_next_key:
                last_key_error = exc
                if index < len(keys) - 1:
                    _progress(
                        progress,
                        f"[WARNING] API key {mask_api_key(api_key)} failed: {exc.message}. Trying next key...",
                    )
                    continue
            raise exc

    raise last_key_error or YoutubeApiError("invalid_key", "Invalid API key")


def _fetch_latest_videos_with_key(
    channel_input: str,
    api_key: str,
    progress,
    hide_below_duration_enabled: bool,
    min_visible_duration_seconds: int,
    hide_above_duration_enabled: bool,
    max_visible_duration_seconds: int,
) -> tuple[ChannelInfo, list[VideoItem], str]:
    _progress(progress, "[INFO] Resolving channel...")
    channel = _resolve_channel(channel_input, api_key)

    _progress(progress, "[INFO] Fetching uploads playlist...")
    uploads_playlist_id = channel.uploads_playlist_id
    if not uploads_playlist_id:
        raise YoutubeApiError("cannot_resolve_channel", "Cannot resolve channel")

    _progress(progress, "[INFO] Fetching latest uploads...")
    videos, next_page_token = _fetch_video_items_until_visible_count(
        uploads_playlist_id,
        api_key,
        start_order=1,
        target_visible_count=VISIBLE_VIDEO_FETCH_TARGET,
        max_checked=MAX_UPLOADS_SCAN_LIMIT,
        hide_below_duration_enabled=hide_below_duration_enabled,
        min_visible_duration_seconds=min_visible_duration_seconds,
        hide_above_duration_enabled=hide_above_duration_enabled,
        max_visible_duration_seconds=max_visible_duration_seconds,
    )
    return channel, videos, next_page_token


def _fetch_more_videos_with_key(
    uploads_playlist_id: str,
    page_token: str,
    start_order: int,
    api_key: str,
    hide_below_duration_enabled: bool,
    min_visible_duration_seconds: int,
    hide_above_duration_enabled: bool,
    max_visible_duration_seconds: int,
) -> tuple[list[VideoItem], str]:
    videos, next_page_token = _fetch_video_items_until_visible_count(
        uploads_playlist_id,
        api_key,
        start_order=start_order,
        target_visible_count=VISIBLE_VIDEO_FETCH_TARGET,
        max_checked=MAX_UPLOADS_SCAN_LIMIT,
        page_token=page_token,
        hide_below_duration_enabled=hide_below_duration_enabled,
        min_visible_duration_seconds=min_visible_duration_seconds,
        hide_above_duration_enabled=hide_above_duration_enabled,
        max_visible_duration_seconds=max_visible_duration_seconds,
    )
    return videos, next_page_token


def _progress(progress, message: str) -> None:
    if progress:
        progress(sanitize_log_text(message))


def _api_get(endpoint: str, params: dict[str, str], api_key: str) -> dict:
    query = dict(params)
    query["key"] = api_key
    url = f"{API_BASE}/{endpoint}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        reason, message = _parse_youtube_http_error(exc)
        if _is_invalid_key(reason, message):
            raise YoutubeApiError("invalid_key", "Invalid API key", retry_next_key=True)
        if _is_quota_error(reason, message):
            raise YoutubeApiError("quota_exceeded", "API quota exceeded", retry_next_key=True)
        raise YoutubeApiError("api_error", _friendly_api_message(exc.code, reason, message))
    except (urllib.error.URLError, TimeoutError):
        raise YoutubeApiError("network", "Network error")

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise YoutubeApiError("api_error", "Network error")


def _parse_youtube_http_error(exc: urllib.error.HTTPError) -> tuple[str, str]:
    try:
        payload = exc.read().decode("utf-8", errors="replace")
        data = json.loads(payload)
        error = data.get("error", {})
        errors = error.get("errors", [])
        reason = errors[0].get("reason", "") if errors else error.get("status", "")
        message = error.get("message", "")
        return reason or "", message or ""
    except Exception:
        return "", ""


def _is_invalid_key(reason: str, message: str) -> bool:
    haystack = f"{reason} {message}".lower()
    return "keyinvalid" in haystack or "api key not valid" in haystack or "invalid api key" in haystack


def _is_quota_error(reason: str, message: str) -> bool:
    haystack = f"{reason} {message}".lower()
    quota_markers = (
        "quota",
        "dailylimitexceeded",
        "ratelimitexceeded",
        "userratelimitexceeded",
    )
    return any(marker in haystack for marker in quota_markers)


def _friendly_api_message(status_code: int, reason: str, message: str) -> str:
    haystack = f"{reason} {message}".lower()
    if status_code in (401, 403) and "quota" in haystack:
        return "API quota exceeded"
    if status_code in (400, 403) and ("channel" in haystack or "not found" in haystack):
        return "Cannot resolve channel"
    return "Network error" if status_code >= 500 else "Cannot resolve channel"


def _resolve_channel(channel_input: str, api_key: str) -> ChannelInfo:
    candidates = _channel_candidates(channel_input)
    if not candidates:
        raise YoutubeApiError("invalid_channel_url", "Cannot resolve channel")

    for param_name, value in candidates:
        data = _api_get(
            "channels",
            {
                "part": "snippet,contentDetails",
                param_name: value,
                "maxResults": "1",
            },
            api_key,
        )
        items = data.get("items", [])
        if items:
            return _channel_from_item(items[0])

    raise YoutubeApiError("cannot_resolve_channel", "Cannot resolve channel")


def _channel_candidates(channel_input: str) -> list[tuple[str, str]]:
    raw = (channel_input or "").strip()
    if not raw:
        return []

    candidates: list[tuple[str, str]] = []

    def add(param: str, value: str) -> None:
        value = (value or "").strip().strip("/")
        if value and (param, value) not in candidates:
            candidates.append((param, value))

    text = raw
    if re.match(r"^UC[A-Za-z0-9_-]{20,}$", text):
        add("id", text)

    parse_text = text
    if "youtube.com" in parse_text and not parse_text.startswith(("http://", "https://")):
        parse_text = "https://" + parse_text

    parsed = urllib.parse.urlparse(parse_text)
    if parsed.scheme and parsed.netloc:
        segments = [
            urllib.parse.unquote(segment)
            for segment in parsed.path.split("/")
            if segment
        ]
        for index, segment in enumerate(segments):
            lower = segment.lower()
            if lower == "channel" and index + 1 < len(segments):
                add("id", segments[index + 1])
            elif lower == "user" and index + 1 < len(segments):
                add("forUsername", segments[index + 1])
            elif lower == "c" and index + 1 < len(segments):
                add("forUsername", segments[index + 1])
            elif segment.startswith("@"):
                _add_handle_candidates(add, segment)
    else:
        if text.startswith("@"):
            _add_handle_candidates(add, text)
        elif not re.match(r"^UC[A-Za-z0-9_-]{20,}$", text):
            _add_handle_candidates(add, text)
            add("forUsername", text)

    return candidates


def _add_handle_candidates(add, handle: str) -> None:
    handle = handle.strip().strip("/")
    if not handle:
        return
    no_at = handle[1:] if handle.startswith("@") else handle
    add("forHandle", no_at)
    add("forHandle", f"@{no_at}")


def _channel_from_item(item: dict) -> ChannelInfo:
    snippet = item.get("snippet", {})
    content_details = item.get("contentDetails", {})
    related = content_details.get("relatedPlaylists", {})
    return ChannelInfo(
        channel_id=item.get("id", ""),
        channel_name=snippet.get("title", "Channel"),
        uploads_playlist_id=related.get("uploads", ""),
    )


def _fetch_upload_video_ids(
    playlist_id: str,
    api_key: str,
    limit: int = 100,
    page_token: str = "",
) -> tuple[list[str], str]:
    video_ids: list[str] = []
    current_page_token = page_token

    for _ in range(2):
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": "50",
        }
        if current_page_token:
            params["pageToken"] = current_page_token

        data = _api_get("playlistItems", params, api_key)
        next_page_token = data.get("nextPageToken", "")
        for item in data.get("items", []):
            video_id = item.get("contentDetails", {}).get("videoId", "")
            if video_id:
                video_ids.append(video_id)
                if len(video_ids) >= limit:
                    return video_ids, next_page_token

        current_page_token = next_page_token
        if not current_page_token:
            break

    return video_ids, current_page_token


def _fetch_video_items_until_visible_count(
    playlist_id: str,
    api_key: str,
    start_order: int = 1,
    target_visible_count: int = VISIBLE_VIDEO_FETCH_TARGET,
    max_checked: int = MAX_UPLOADS_SCAN_LIMIT,
    page_token: str = "",
    hide_below_duration_enabled: bool = True,
    min_visible_duration_seconds: int = DURATION_FILTER_DEFAULT_HIDE_BELOW_SECONDS,
    hide_above_duration_enabled: bool = True,
    max_visible_duration_seconds: int = DURATION_FILTER_DEFAULT_HIDE_ABOVE_SECONDS,
) -> tuple[list[VideoItem], str]:
    videos: list[VideoItem] = []
    visible_count = 0
    checked_count = 0
    current_page_token = page_token

    while checked_count < max_checked and visible_count < target_visible_count:
        remaining = max_checked - checked_count
        page_limit = min(50, remaining)
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": str(page_limit),
        }
        if current_page_token:
            params["pageToken"] = current_page_token

        data = _api_get("playlistItems", params, api_key)
        next_page_token = data.get("nextPageToken", "")
        video_ids = []
        for item in data.get("items", []):
            video_id = item.get("contentDetails", {}).get("videoId", "")
            if video_id:
                video_ids.append(video_id)

        if not video_ids:
            return videos, next_page_token

        page_videos = _fetch_video_details(video_ids, api_key, start_order=start_order + len(videos))
        videos.extend(page_videos)
        checked_count += len(video_ids)
        visible_count += sum(
            1
            for video in page_videos
            if _counts_toward_visible_target(
                video,
                hide_below_enabled=hide_below_duration_enabled,
                min_duration_seconds=min_visible_duration_seconds,
                hide_above_enabled=hide_above_duration_enabled,
                max_duration_seconds=max_visible_duration_seconds,
            )
        )

        current_page_token = next_page_token
        if not current_page_token:
            break

    return videos, current_page_token


def _fetch_video_details(video_ids: list[str], api_key: str, start_order: int = 1) -> list[VideoItem]:
    details_by_id: dict[str, VideoItem] = {}
    for start in range(0, len(video_ids), 50):
        chunk = video_ids[start : start + 50]
        data = _api_get(
            "videos",
            {
                "part": "snippet,contentDetails",
                "id": ",".join(chunk),
            },
            api_key,
        )
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            content_details = item.get("contentDetails", {})
            video_id = item.get("id", "")
            if not video_id:
                continue
            duration_iso = content_details.get("duration", "")
            duration_seconds = _parse_duration_seconds(duration_iso)
            live_broadcast_content = _normalize_live_broadcast_content(
                snippet.get("liveBroadcastContent", "none")
            )
            details_by_id[video_id] = VideoItem(
                video_id=video_id,
                title=snippet.get("title", "(Untitled)"),
                duration=_duration_to_text(duration_iso, live_broadcast_content),
                published_at=_published_to_date(snippet.get("publishedAt", "")),
                thumbnail_url=_best_thumbnail_url(snippet.get("thumbnails", {})),
                display_order=0,
                duration_seconds=duration_seconds,
                live_broadcast_content=live_broadcast_content,
            )

    ordered: list[VideoItem] = []
    for video_id in video_ids:
        item = details_by_id.get(video_id)
        if item:
            item.display_order = start_order + len(ordered)
            ordered.append(item)
    return ordered


def _best_thumbnail_url(thumbnails: dict) -> str:
    for key in ("maxres", "standard", "high", "medium", "default"):
        url = thumbnails.get(key, {}).get("url", "")
        if url:
            return url
    return ""


def is_video_visible_by_duration(
    video: VideoItem,
    *,
    hide_below_enabled: bool = True,
    min_duration_seconds: int = DURATION_FILTER_DEFAULT_HIDE_BELOW_SECONDS,
    hide_above_enabled: bool = True,
    max_duration_seconds: int = DURATION_FILTER_DEFAULT_HIDE_ABOVE_SECONDS,
) -> bool:
    if getattr(video, "live_broadcast_content", "none") in {"live", "upcoming"}:
        return True
    try:
        seconds = getattr(video, "duration_seconds")
    except Exception:
        return True
    if seconds is None:
        return True
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        return True
    if seconds <= 0:
        return True

    if hide_below_enabled and seconds < _normalize_duration_threshold_seconds(
        min_duration_seconds,
        DURATION_FILTER_DEFAULT_HIDE_BELOW_SECONDS,
    ):
        return False
    if hide_above_enabled and seconds > _normalize_duration_threshold_seconds(
        max_duration_seconds,
        DURATION_FILTER_DEFAULT_HIDE_ABOVE_SECONDS,
    ):
        return False
    return True


def _counts_toward_visible_target(
    video: VideoItem,
    *,
    hide_below_enabled: bool,
    min_duration_seconds: int,
    hide_above_enabled: bool,
    max_duration_seconds: int,
) -> bool:
    return is_video_visible_by_duration(
        video,
        hide_below_enabled=hide_below_enabled,
        min_duration_seconds=min_duration_seconds,
        hide_above_enabled=hide_above_enabled,
        max_duration_seconds=max_duration_seconds,
    )


def _duration_to_text(duration: str, live_broadcast_content: str = "none") -> str:
    live_state = _normalize_live_broadcast_content(live_broadcast_content)
    if live_state == "live":
        return "Đang trực tiếp"
    if live_state == "upcoming":
        return "Sắp phát"

    seconds_total = _parse_duration_seconds(duration)
    if seconds_total is None:
        return "Không rõ"

    hours = seconds_total // 3600
    minutes = (seconds_total % 3600) // 60
    seconds = seconds_total % 60

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _duration_to_seconds(duration: str) -> int | None:
    return _parse_duration_seconds(duration)


def _parse_duration_seconds(duration: str) -> int | None:
    if not isinstance(duration, str):
        return None

    value = duration.strip()
    if not value:
        return None

    match = _DURATION_PATTERN.fullmatch(value)
    if match is None:
        return None

    days_raw = match.group("days")
    hours_raw = match.group("hours")
    minutes_raw = match.group("minutes")
    seconds_raw = match.group("seconds")
    has_date_component = days_raw is not None
    has_time_component = any(part is not None for part in (hours_raw, minutes_raw, seconds_raw))
    if not has_date_component and not has_time_component:
        return None
    if "T" in value and not has_time_component:
        return None

    days = int(days_raw or 0)
    hours = int(hours_raw or 0)
    minutes = int(minutes_raw or 0)
    seconds = int(seconds_raw or 0)
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        return None
    return total


def _normalize_live_broadcast_content(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"live", "upcoming"}:
        return normalized
    return "none"


def _normalize_duration_threshold_seconds(threshold_seconds: int, default_seconds: int) -> int:
    try:
        value = int(threshold_seconds)
    except (TypeError, ValueError):
        value = default_seconds
    if value <= 0:
        value = default_seconds
    return value


def _published_to_date(published_at: str) -> str:
    return (published_at or "")[:10]
