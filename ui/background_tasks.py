from core.download_service import DownloadError, download_items
from core.error_messages import classify_api_error, classify_general_error, format_friendly_error
from core.file_status import apply_statuses
from core.progress_status import ProgressEvent
from core.youtube_api import (
    YoutubeApiError,
    fetch_latest_video_page,
    fetch_more_videos,
    is_video_visible_by_duration,
    sanitize_log_text,
)
from ui.session_state import ChannelRequestContext, FetchRequestToken, LoadMoreRequestToken


def run_fetch_task(
    request_token: FetchRequestToken,
    manual_key: str,
    events,
    *,
    fetch_page=fetch_latest_video_page,
    reconcile_statuses=apply_statuses,
    friendly_api_error=None,
    friendly_general_error=None,
) -> None:
    api_error = friendly_api_error or _friendly_api_message
    general_error = friendly_general_error or _friendly_general_message
    try:
        context = request_token.context
        log = lambda message: publish_channel_request_log(events, request_token, message)
        channel, videos, next_page_token = fetch_page(
            request_token.channel_input,
            manual_key,
            progress=log,
            hide_below_duration_enabled=context.hide_below_enabled,
            min_visible_duration_seconds=context.hide_below_minutes * 60,
            hide_above_duration_enabled=context.hide_above_enabled,
            max_visible_duration_seconds=context.hide_above_minutes * 60,
        )
        log("[INFO] Checking local files...")
        reconcile_statuses(
            videos,
            context.save_folder,
            channel.channel_name,
            channel.channel_id,
            download_mode=context.download_mode,
            warning_callback=log,
        )
        hidden_by_duration = duration_hidden_count(videos, context)
        if hidden_by_duration:
            log(f"[INFO] Đã ẩn {hidden_by_duration} video theo thời lượng.")
        visible_count = len(videos) - hidden_by_duration
        log(f"[SUCCESS] Đã nạp {visible_count} video sau khi lọc thời lượng.")
        if not next_page_token:
            log("[INFO] Không còn video nào.")
        events.put(("fetch_done", request_token, channel, videos, next_page_token))
    except YoutubeApiError as exc:
        events.put(("fetch_error", request_token, api_error(exc)))
    except Exception as exc:
        events.put(("fetch_error", request_token, general_error(str(exc) or "Network error")))


def run_load_more_task(
    request_token: LoadMoreRequestToken,
    manual_key: str,
    events,
    *,
    fetch_more=fetch_more_videos,
    friendly_api_error=None,
    friendly_general_error=None,
) -> None:
    api_error = friendly_api_error or _friendly_api_message
    general_error = friendly_general_error or _friendly_general_message
    try:
        context = request_token.context
        log = lambda message: publish_channel_request_log(events, request_token, message)
        log("[INFO] Đang nạp thêm 100 video tiếp theo...")
        videos, next_page_token = fetch_more(
            request_token.uploads_playlist_id,
            request_token.page_token,
            request_token.start_order,
            manual_key,
            progress=log,
            hide_below_duration_enabled=context.hide_below_enabled,
            min_visible_duration_seconds=context.hide_below_minutes * 60,
            hide_above_duration_enabled=context.hide_above_enabled,
            max_visible_duration_seconds=context.hide_above_minutes * 60,
        )
        hidden_by_duration = duration_hidden_count(videos, context)
        if hidden_by_duration:
            log(f"[INFO] Đã ẩn {hidden_by_duration} video theo thời lượng.")
        if videos:
            visible_count = len(videos) - hidden_by_duration
            log(f"[SUCCESS] Đã nạp thêm {visible_count} video sau khi lọc thời lượng.")
        if not next_page_token:
            log("[INFO] Không còn video nào.")
        events.put(("load_more_done", request_token, videos, next_page_token))
    except YoutubeApiError as exc:
        events.put(("load_more_error", request_token, api_error(exc)))
    except Exception as exc:
        events.put(("load_more_error", request_token, general_error(str(exc) or "Network error")))


def run_download_task(
    selected,
    options,
    controller,
    download_run_id: int,
    events,
    *,
    log_callback,
    status_callback,
    progress_callback,
    download_batch=download_items,
    friendly_general_error=None,
) -> None:
    general_error = friendly_general_error or _friendly_general_message
    outcome = "completed"
    message = ""
    try:
        download_batch(
            selected,
            options,
            log_callback,
            lambda video: status_callback(video, download_run_id),
            cancel_controller=controller,
            progress_callback=progress_callback,
        )
    except DownloadError as exc:
        outcome = "error"
        message = general_error(str(exc))
        progress_callback(ProgressEvent(kind="error", phase="Lỗi", message=message))
    except Exception as exc:
        outcome = "error"
        message = general_error(str(exc))
        progress_callback(ProgressEvent(kind="error", phase="Lỗi", message=message))
    finally:
        events.put(("download_worker_finished", outcome, message))


def publish_channel_request_log(events, request_token, message: str) -> None:
    events.put(("channel_request_log", request_token, sanitize_log_text(message)))


def duration_hidden_count(videos: list, context: ChannelRequestContext) -> int:
    return sum(
        1
        for video in videos
        if not is_video_visible_by_duration(
            video,
            hide_below_enabled=context.hide_below_enabled,
            min_duration_seconds=context.hide_below_minutes * 60,
            hide_above_enabled=context.hide_above_enabled,
            max_duration_seconds=context.hide_above_minutes * 60,
        )
    )


def _friendly_api_message(exc: YoutubeApiError) -> str:
    return format_friendly_error(classify_api_error(exc.code, exc.message), [exc.message])


def _friendly_general_message(message: str) -> str:
    return format_friendly_error(classify_general_error(message), [message])
