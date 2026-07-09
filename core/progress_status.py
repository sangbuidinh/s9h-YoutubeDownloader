import queue
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    kind: str = "phase"
    phase: str = ""
    message: str = ""
    video_index: int | None = None
    video_total: int | None = None
    title: str | None = None
    percent: str | None = None
    speed: str | None = None
    eta: str | None = None
    fragment: str | None = None


def put_latest_progress_event(progress_queue: queue.Queue, event: ProgressEvent) -> None:
    try:
        while True:
            progress_queue.get_nowait()
    except queue.Empty:
        pass

    try:
        progress_queue.put_nowait(event)
        return
    except queue.Full:
        pass

    try:
        progress_queue.get_nowait()
    except queue.Empty:
        pass

    try:
        progress_queue.put_nowait(event)
    except queue.Full:
        pass


def parse_ytdlp_progress_line(line: str) -> dict | None:
    text = (line or "").strip()
    if not text:
        return None

    lower = text.lower()
    if lower.startswith("[download]"):
        fragment_match = re.search(r"downloading\s+fragment\s+(\d+)\s+of\s+(\d+)", text, re.IGNORECASE)
        if fragment_match:
            return {
                "phase": "download",
                "message": "Downloading fragment",
                "fragment": f"{fragment_match.group(1)}/{fragment_match.group(2)}",
            }

        percent_match = re.search(r"\[download\]\s+(\d+(?:\.\d+)?%)", text, re.IGNORECASE)
        if percent_match:
            result = {
                "phase": "download",
                "percent": percent_match.group(1),
            }
            speed_match = re.search(r"\bat\s+([^\s]+)\s+ETA\b", text, re.IGNORECASE)
            eta_match = re.search(r"\bETA\s+([0-9:]+)", text, re.IGNORECASE)
            if speed_match:
                result["speed"] = speed_match.group(1)
            if eta_match:
                result["eta"] = eta_match.group(1)
            return result

        if lower.startswith("[download] destination:"):
            return {"phase": "download", "message": "Preparing download"}
        if "has already been downloaded" in lower:
            return {"phase": "download", "message": "Already downloaded"}
        return None

    if lower.startswith("[merger]"):
        return {"phase": "merge", "message": "Merging formats"}
    if lower.startswith("[extractaudio]"):
        return {"phase": "audio", "message": "Extracting audio"}
    if lower.startswith("[ffmpeg]"):
        return {"phase": "postprocess", "message": "Post-processing"}
    return None


def format_progress_event_lines(event: ProgressEvent) -> tuple[str, str]:
    if event.kind == "ready":
        return "Downloading: Ready", "Processing: -"
    if event.kind == "batch_complete":
        return "Downloading: Batch completed", "Processing: -"
    if event.kind == "stop_requested":
        return "Downloading: Stop requested", "Processing: Cancelling current process..."

    phase = _display_phase(event)
    current_parts = [phase]
    filename = _display_filename(event, phase)
    if filename:
        current_parts.append(filename)

    return f"Downloading: {' | '.join(current_parts)}", f"Processing: {_detail_text(event, phase)}"


def format_progress_event(event: ProgressEvent) -> str:
    return " ".join(format_progress_event_lines(event))


def _display_phase(event: ProgressEvent) -> str:
    if event.kind == "error":
        return "Error"
    phase = _compact_text(event.phase or _label_for_kind(event.kind), 48)
    lower = phase.lower()
    if lower in ("thumb", "thumbnail"):
        return "Thumbnail"
    if lower in ("audio", "mp3"):
        return "MP3"
    if lower == "validating mp4":
        return "Video"
    return phase or "Status"


def _detail_text(event: ProgressEvent, phase: str) -> str:
    message = _compact_text(event.message, 72)
    if event.kind == "error" and message:
        return message
    if event.kind == "ffmpeg_progress":
        return _ffmpeg_progress_detail_text(event)
    if _is_priority_progress_message(message):
        return message

    details = _progress_detail_parts(event)
    if details:
        return " | ".join(details)

    if message:
        return message
    if (event.phase or "").lower() == "validating mp4":
        return "Validating MP4"
    if phase in ("Video", "MP3"):
        return "Downloading..."
    if phase == "Thumbnail":
        return "Working..."
    if phase in ("Completed", "Skipped"):
        return phase
    return "-"


def _progress_detail_parts(event: ProgressEvent) -> list[str]:
    parts = []
    if event.percent:
        parts.append(_compact_text(event.percent, 24))
    if event.fragment and not event.percent:
        parts.append(f"Fragment {_compact_text(event.fragment, 24)}")
    if event.speed:
        parts.append(f"yt-dlp {_compact_text(event.speed, 32)}")
    if event.fragment and event.percent:
        parts.append(f"Fragment {_compact_text(event.fragment, 24)}")
    return parts


def _ffmpeg_progress_detail_text(event: ProgressEvent) -> str:
    parts = []
    filename = _ffmpeg_progress_filename(event)
    if filename:
        parts.append(filename)
    if event.percent:
        parts.append(_compact_text(event.percent, 24))
    if event.speed:
        parts.append(f"speed {_compact_text(event.speed, 32)}")
    return " | ".join(parts) if parts else "-"


def _ffmpeg_progress_filename(event: ProgressEvent) -> str:
    title = _compact_text(event.title, 90)
    if not title:
        return ""
    filename = _safe_display_filename(title)
    if not filename.lower().endswith(".mp4"):
        filename = f"{filename}.mp4"
    return _compact_text(filename, 96)


def _is_priority_progress_message(message: str) -> bool:
    return message in {
        "Preparing download",
        "Downloading...",
        "Merging formats",
        "Post-processing",
        "Extracting audio from MP4",
        "Validating MP4",
        "Downloading image",
        "Using yt-dlp fallback",
        "Completed",
        "Skipped",
    }


def _display_filename(event: ProgressEvent, phase: str) -> str:
    title = _compact_text(event.title, 90)
    if not title:
        return ""
    filename = _safe_display_filename(title)
    extension = _extension_for_phase(event.phase, phase)
    if extension and not filename.lower().endswith(extension):
        filename = f"{filename}{extension}"
    return _compact_text(filename, 96)


def _extension_for_phase(raw_phase: str, display_phase: str) -> str:
    lower = (raw_phase or display_phase or "").lower()
    if lower == "validating mp4" or display_phase == "Video":
        return ".mp4"
    if display_phase == "Thumbnail":
        return ".jpg"
    if display_phase == "MP3":
        return ".mp3"
    return ""


def _safe_display_filename(text: str) -> str:
    value = sanitize_progress_text(text)
    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    value = value.strip(" .")
    return value or "item"


def sanitize_progress_text(text: str | None) -> str:
    value = str(text or "")
    value = value.replace("\r", " ").replace("\n", " ")
    youtube_api_key_prefix = "AI" "za"
    value = re.sub(r"(?i)([?&]key=)[^&\s]+", r"\1***", value)
    value = re.sub(r"(?i)(\bkey=)[^&\s]+", r"\1***", value)
    value = re.sub(
        re.escape(youtube_api_key_prefix) + r"[0-9A-Za-z_-]{20,}",
        youtube_api_key_prefix + "...****",
        value,
    )
    value = re.sub(r"(?i)(cookie(?:s)?\s*[:=])\s*[^|]+", r"\1 ***", value)
    value = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)\S+", r"\1***", value)
    value = re.sub(r"(?i)(x-goog-api-key\s*[:=])\s*\S+", r"\1 ***", value)
    value = re.sub(r"(?i)(token\s*[:=])\s*\S+", r"\1 ***", value)
    value = re.sub(
        r"(?i)\b(SID|SAPISID|HSID|SSID|APISID|LOGIN_INFO|VISITOR_INFO1_LIVE|__Secure-[A-Za-z0-9_-]+)=\S+",
        r"\1=***",
        value,
    )
    return " ".join(value.split())


def _compact_text(text: str | None, limit: int) -> str:
    value = sanitize_progress_text(text)
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


def _label_for_kind(kind: str) -> str:
    if kind == "error":
        return "Error"
    if kind == "completed":
        return "Completed"
    if kind == "skipped":
        return "Skipped"
    return "Status"


def _index_text(video_index: int | None, video_total: int | None) -> str:
    if video_index is None:
        return ""
    if video_total is None:
        return str(video_index)
    return f"{video_index}/{video_total}"
