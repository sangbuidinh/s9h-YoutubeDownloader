import re
from dataclasses import dataclass


SHOW_TECHNICAL_ERRORS = False
SHOW_TECHNICAL_WARNINGS = False


@dataclass(frozen=True)
class FriendlyError:
    level: str
    title: str
    reason: str
    actions: tuple[str, ...]


def format_friendly_error(
    error: FriendlyError,
    technical_lines: list[str] | tuple[str, ...] | None = None,
) -> str:
    lines = [
        f"[{error.level}] {error.title}",
        f"Lý do: {error.reason}",
        "Cách xử lý:",
    ]
    lines.extend(f"- {action}" for action in error.actions)

    detail = _technical_detail(technical_lines or [])
    if detail:
        if "\n" in detail:
            lines.append("Chi tiết kỹ thuật:")
            lines.extend(detail.splitlines())
        else:
            lines.append(f"Chi tiết kỹ thuật: {detail}")
    return "\n".join(lines)


def classify_ytdlp_error(
    text: str,
    cookies_enabled: bool = False,
    bot_check: bool = False,
    http_403: bool = False,
    missing_js_runtime: bool = False,
) -> FriendlyError:
    if bot_check or _contains_bot_check(text):
        return FRIENDLY_ERRORS["bot_verification"]
    if cookies_enabled and _contains_cookie_session_rejected(text):
        return FRIENDLY_ERRORS["cookie_session_rejected"]
    if http_403 or _contains_http_403(text):
        return FRIENDLY_ERRORS["http_403_repeated"]
    if missing_js_runtime or _contains_js_runtime(text):
        return FRIENDLY_ERRORS["missing_js_runtime"]
    return classify_general_error(text)


def classify_api_error(code: str, message: str = "") -> FriendlyError:
    haystack = f"{code} {message}".lower()
    if "api_key_file_error" in haystack:
        return FRIENDLY_ERRORS["api_key_file_error"]
    if _contains_invalid_api_key(haystack):
        return FRIENDLY_ERRORS["invalid_api_key"]
    if _contains_quota(haystack):
        return FRIENDLY_ERRORS["api_quota"]
    if _contains_channel_resolution(haystack):
        return FRIENDLY_ERRORS["cannot_resolve_channel"]
    if _contains_network(haystack):
        return FRIENDLY_ERRORS["network"]
    return FRIENDLY_ERRORS["cannot_resolve_channel"]


def classify_general_error(text: str) -> FriendlyError:
    lower = (text or "").lower()
    if _contains_cookies_missing(lower):
        return FRIENDLY_ERRORS["cookies_missing"]
    if "no save folder selected" in lower:
        return FRIENDLY_ERRORS["no_save_folder"]
    if "no selected videos" in lower:
        return FRIENDLY_ERRORS["no_selected_videos"]
    if "no videos loaded" in lower:
        return FRIENDLY_ERRORS["no_videos_loaded"]
    if "invalid speed limit" in lower:
        return FRIENDLY_ERRORS["invalid_speed_limit"]
    if _contains_missing_ytdlp(lower):
        return FRIENDLY_ERRORS["missing_ytdlp"]
    if _contains_missing_ffmpeg(lower):
        return FRIENDLY_ERRORS["missing_ffmpeg"]
    if _contains_sqlite_state_error(lower):
        return FRIENDLY_ERRORS["sqlite_state"]
    if _contains_premiere_safe_mp4_error(lower):
        return FRIENDLY_ERRORS["premiere_safe_mp4"]
    if _contains_stream_interrupted(lower):
        return FRIENDLY_ERRORS["stream_interrupted"]
    if _contains_disk_full(lower):
        return FRIENDLY_ERRORS["disk_full"]
    if _contains_audio_extraction(lower):
        return FRIENDLY_ERRORS["audio_failed"]
    if _contains_file_exists(lower):
        return FRIENDLY_ERRORS["file_exists"]
    if _contains_invalid_filename(lower):
        return FRIENDLY_ERRORS["invalid_filename"]
    if _contains_path_too_long(lower):
        return FRIENDLY_ERRORS["path_too_long"]
    if _contains_file_in_use(lower):
        return FRIENDLY_ERRORS["file_in_use"]
    if _contains_permission(lower):
        return FRIENDLY_ERRORS["permission_denied"]
    if _contains_unknown_file_operation(lower):
        return FRIENDLY_ERRORS["unknown_file_operation"]
    if _contains_thumbnail(lower):
        return FRIENDLY_ERRORS["thumbnail_failed"]
    if _contains_interrupted(lower):
        return FRIENDLY_ERRORS["interrupted"]
    if _contains_codec_warning(lower):
        return FRIENDLY_ERRORS["unsupported_codec"]
    if _contains_network(lower):
        return FRIENDLY_ERRORS["network"]
    if _contains_invalid_api_key(lower):
        return FRIENDLY_ERRORS["invalid_api_key"]
    if _contains_quota(lower):
        return FRIENDLY_ERRORS["api_quota"]
    if _contains_channel_resolution(lower):
        return FRIENDLY_ERRORS["cannot_resolve_channel"]
    return FRIENDLY_ERRORS["generic"]


def missing_js_runtime_warning() -> FriendlyError:
    return FRIENDLY_ERRORS["missing_js_runtime"]


def batch_blocked_warning() -> FriendlyError:
    return FRIENDLY_ERRORS["batch_blocked"]


def friendly_ytdlp_failure_kind_error(kind: str, refreshed_rejected: bool = False) -> FriendlyError:
    if refreshed_rejected:
        return FRIENDLY_ERRORS["refreshed_cookie_rejected"]
    mapping = {
        "http_401": "cookie_session_rejected",
        "rate_limit": "rate_limit",
        "rate_limit_429": "rate_limit",
        "bot_check": "bot_verification",
        "cookie_session": "cookie_session_rejected",
        "login_required": "cookie_session_rejected",
        "po_token_or_visitor_data": "bot_verification",
        "http_403": "http_403_repeated",
        "format_unavailable": "premiere_safe_mp4",
        "network_timeout": "network",
        "network": "network",
        "output_path": "invalid_filename",
    }
    return FRIENDLY_ERRORS.get(mapping.get(kind, ""), FRIENDLY_ERRORS["generic"])


def friendly_ffmpeg_failure_kind_error(kind: str, text: str = "") -> FriendlyError:
    normalized = (kind or "").strip().lower()
    mapping = {
        "invalid_input": "ffmpeg_invalid_input",
        "no_audio_stream": "ffmpeg_no_audio",
        "encoder_unavailable": "ffmpeg_encoder_unavailable",
        "disk_full": "disk_full",
        "permission_denied": "permission_denied",
    }
    if normalized == "output_path":
        lower = (text or "").lower()
        if _contains_path_too_long(lower):
            return FRIENDLY_ERRORS["path_too_long"]
        if _contains_permission(lower):
            return FRIENDLY_ERRORS["permission_denied"]
        return FRIENDLY_ERRORS["invalid_filename"]
    if normalized == "interrupted_write":
        return FRIENDLY_ERRORS["audio_failed"]
    return FRIENDLY_ERRORS.get(mapping.get(normalized, ""), FRIENDLY_ERRORS["audio_failed"])


def _technical_detail(technical_lines: list[str] | tuple[str, ...]) -> str:
    lines = [str(line).strip() for line in technical_lines if str(line).strip()]
    if not lines:
        return ""
    if SHOW_TECHNICAL_ERRORS:
        return "\n".join(lines[-50:])
    return _shorten(lines[0], 260)


def _shorten(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _contains_bot_check(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "sign in to confirm you're not a bot" in lower
        or "sign in to confirm you are not a bot" in lower
        or "not a bot" in lower
        or "confirm you're not a bot" in lower
        or "confirm you are not a bot" in lower
        or "this helps protect our community" in lower
        or "verify that you're human" in lower
        or "verify you're human" in lower
        or "verify that you are human" in lower
        or "verify you are human" in lower
        or "unusual traffic" in lower
        or "automated requests" in lower
        or "automated request" in lower
        or "detected automated traffic" in lower
    )


def _contains_cookie_session_rejected(text: str) -> bool:
    lower = (text or "").lower()
    if not lower:
        return False
    session_markers = (
        "cookies are expired",
        "cookies expired",
        "cookies are invalid",
        "cookie invalid",
        "cookies invalid",
        "cookie is invalid",
        "cookies are no longer valid",
        "cookie is no longer valid",
        "cookie/session rejected",
        "cookie session rejected",
        "session cookie rejected",
        "youtube rejected the supplied session",
        "supplied browser session has expired",
        "browser session has expired",
        "login session expired",
        "session has expired",
        "current account is not authenticated",
        "account is not authenticated",
        "account authentication failed",
        "not authenticated",
        "authentication required",
        "authentication is required",
        "authentication is required to view this video",
        "login required",
        "please log in",
        "sign in to continue",
        "please sign in to continue",
        "please sign in to view this video",
        "you must be signed in to view this video",
        "sign in to confirm your age",
        "sign in to confirm your identity",
        "sign in to confirm your account",
        "failed to load cookies",
        "could not load cookies",
        "unable to load cookies",
        "failed to parse cookies",
        "could not parse cookies",
        "unable to parse cookies",
    )
    if any(marker in lower for marker in session_markers):
        return True
    if "age-restricted" in lower or "age restricted" in lower:
        return any(marker in lower for marker in ("authenticate", "authentication", "sign in", "login", "cookie"))
    if "cookie" not in lower and "cookies" not in lower and "browser session" not in lower:
        return False
    return (
        "expired" in lower
        or "invalid" in lower
        or "not valid" in lower
        or "no longer valid" in lower
        or "rejected" in lower
        or "authentication" in lower
        or "authenticate" in lower
        or "failed" in lower
        or "could not" in lower
        or "unable to" in lower
    )


def _contains_http_403(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "http error 403" in lower
        or "403: forbidden" in lower
        or re.search(r"\b403\b[^\n\r]*\bforbidden\b", lower) is not None
        or re.search(r"\bforbidden\b[^\n\r]*\b403\b", lower) is not None
    )


def _contains_js_runtime(text: str) -> bool:
    lower = (text or "").lower()
    return "no supported javascript runtime" in lower or "javascript runtime" in lower or "ejs" in lower


def _contains_network(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "network error" in lower
        or "timeout" in lower
        or "timed out" in lower
        or "connectionreseterror" in lower
        or "temporary failure" in lower
        or "unable to download webpage" in lower
    )


def _contains_invalid_api_key(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "invalid_key" in lower
        or "invalid api key" in lower
        or "api key not valid" in lower
        or "keyinvalid" in lower
        or ("badrequest" in lower and "key" in lower)
    )


def _contains_quota(text: str) -> bool:
    lower = (text or "").lower()
    return "quota" in lower or "quotaexceeded" in lower or "dailylimitexceeded" in lower


def _contains_channel_resolution(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "cannot resolve channel" in lower
        or "channel not found" in lower
        or "invalid channel" in lower
        or "no uploads playlist" in lower
        or "invalid_channel_url" in lower
        or "cannot_resolve_channel" in lower
    )


def _contains_cookies_missing(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "cookies file missing" in lower
        or "cookies.txt missing" in lower
        or "no cookies file selected" in lower
        or "chưa chọn cookies" in lower
    )


def _contains_missing_ytdlp(text: str) -> bool:
    lower = (text or "").lower()
    return "yt-dlp.exe missing" in lower or ("filenotfounderror" in lower and "yt-dlp" in lower)


def _contains_missing_ffmpeg(text: str) -> bool:
    lower = (text or "").lower()
    return "ffmpeg.exe missing" in lower or "ffmpeg not found" in lower


def _contains_premiere_safe_mp4_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "premiere_safe_mp4_validation_failed" in lower
        or "requested format is not available" in lower
        or "requested format not available" in lower
        or "no video formats found" in lower
        or "no suitable formats" in lower
    )


def _contains_sqlite_state_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "không thể mở cơ sở dữ liệu sqlite" in lower
        or "sqlite database" in lower
        or "sqlite3." in lower
        or "database disk image is malformed" in lower
        or "database is locked" in lower
        or "unable to open database file" in lower
    )


def _contains_path_too_long(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "file name too long" in lower
        or "path too long" in lower
        or "winerror 206" in lower
        or "output path too long" in lower
        or ("oserror" in lower and "path" in lower and "long" in lower)
    )


def _contains_permission(text: str) -> bool:
    lower = (text or "").lower()
    return "permission denied" in lower or "access is denied" in lower or "winerror 5" in lower


def _contains_disk_full(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "no space left on device" in lower
        or "disk full" in lower
        or "not enough space" in lower
        or "there is not enough space on the disk" in lower
    )


def _contains_file_in_use(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "winerror 32" in lower
        or "being used by another process" in lower
        or "the process cannot access the file" in lower
    )


def _contains_file_exists(text: str) -> bool:
    lower = (text or "").lower()
    return "fileexistserror" in lower or "already exists" in lower


def _contains_invalid_filename(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "invalid filename" in lower
        or "invalid argument" in lower
        or "errno 22" in lower
        or "winerror 123" in lower
        or "the filename, directory name, or volume label syntax is incorrect" in lower
    )


def _contains_stream_interrupted(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "bytes read" in lower
        or "more expected" in lower
        or "read timed out" in lower
        or "fragment" in lower
    )


def _contains_unknown_file_operation(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "file operation failed" in lower
        or "file operation error" in lower
        or "failed during move" in lower
        or "failed during state save" in lower
    )


def _contains_thumbnail(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "thumbnail failed" in lower
        or "convert thumbnail failed" in lower
        or "unable to download thumbnail" in lower
        or "thumbnail download failed" in lower
    )


def _contains_audio_extraction(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "audio extraction failed" in lower
        or "expected .mp3 file was not created" in lower
        or ("postprocess" in lower and "audio" in lower)
        or ("post-processing" in lower and "audio" in lower)
    )


def _contains_interrupted(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "user cancel" in lower
        or "process terminated" in lower
        or "download cancelled" in lower
        or "download canceled" in lower
        or "interrupted" in lower
    )


def _contains_codec_warning(text: str) -> bool:
    lower = (text or "").lower()
    return "av1" in lower or "unsupported codec" in lower


FRIENDLY_ERRORS = {
    "bot_check": FriendlyError(
        "ERROR",
        "YouTube đang yêu cầu xác thực",
        "YouTube nghi ngờ lượt tải là tự động, thường xảy ra khi tải nhiều video liên tục hoặc chưa dùng cookies.",
        (
            'Bật "Sử dụng Cookies"',
            "Chọn cookies.txt được xuất từ trình duyệt đang đăng nhập YouTube",
            "Nếu vẫn lỗi, đợi 5-10 phút rồi thử lại",
            "Không tải quá nhiều video liên tục trong thời gian ngắn",
        ),
    ),
    "cookies_missing": FriendlyError(
        "ERROR",
        "Chưa chọn cookies.txt",
        'Bạn đã bật "Sử dụng Cookies" nhưng chưa chọn file cookies.txt hợp lệ.',
        (
            'Bấm "Chọn cookies.txt"',
            "Chọn file cookies.txt đã xuất từ YouTube",
            "Sau đó tải lại",
        ),
    ),
    "cookies_invalid": FriendlyError(
        "ERROR",
        "Cookies không hợp lệ hoặc đã hết hạn",
        "Tool đã dùng cookies.txt nhưng YouTube vẫn yêu cầu xác thực.",
        (
            "Mở trình duyệt đang đăng nhập YouTube",
            "Vào youtube.com và refresh",
            "Xuất lại cookies.txt mới",
            "Chọn lại file cookies.txt trong tool",
        ),
    ),
    "bot_verification": FriendlyError(
        "ERROR",
        "YouTube yêu cầu xác minh người dùng",
        "YouTube đang yêu cầu xác nhận bạn không phải bot. Danh sách tải đã được tạm dừng để tránh tiếp tục gửi yêu cầu.",
        (
            "Mở YouTube trong đúng trình duyệt đang đăng nhập và hoàn tất xác minh nếu có",
            "Xuất lại cookie sau khi phiên trình duyệt đã được YouTube chấp nhận",
            "Chỉ thử lại khi phiên đăng nhập hoạt động bình thường",
        ),
    ),
    "rate_limit": FriendlyError(
        "ERROR",
        "YouTube đang giới hạn lượt tải",
        "YouTube đang tạm giới hạn do có quá nhiều yêu cầu tải. Hãy chờ một thời gian hoặc làm mới phiên cookie trước khi thử lại.",
        (
            "Chờ một thời gian trước khi tải tiếp",
            "Giảm số video tải liên tục",
            "Làm mới cookie nếu YouTube vẫn tiếp tục giới hạn",
        ),
    ),
    "cookie_session_rejected": FriendlyError(
        "ERROR",
        "Phiên đăng nhập không còn hợp lệ",
        "YouTube không chấp nhận phiên đăng nhập hoặc cookie hiện tại. Hãy đăng nhập lại và xuất cookie mới trước khi thử lại.",
        (
            "Mở YouTube trong trình duyệt và đăng nhập lại nếu cần",
            "Xuất hoặc thay thế file cookie sau khi phiên đăng nhập hợp lệ",
            "Chỉ bấm thử lại khi file cookie nguồn đã thay đổi",
        ),
    ),
    "http_403_repeated": FriendlyError(
        "ERROR",
        "YouTube liên tục từ chối truy cập",
        "YouTube đã nhiều lần trả về lỗi HTTP 403. Danh sách tải được tạm dừng để tránh tiếp tục thất bại.",
        (
            "Chờ một thời gian hoặc làm mới cookie trước khi thử lại",
            "Không tiếp tục tải hàng loạt khi YouTube đang từ chối truy cập",
            "Bỏ qua video hiện tại nếu lỗi chỉ xảy ra với video này",
        ),
    ),
    "refreshed_cookie_rejected": FriendlyError(
        "ERROR",
        "Cookie mới vẫn bị YouTube từ chối",
        "Cookie đã được thay đổi nhưng YouTube vẫn không chấp nhận phiên mới. Bạn có thể bỏ qua video hiện tại hoặc dừng danh sách tải.",
        (
            "Đăng nhập lại YouTube trong đúng trình duyệt",
            "Xuất cookie mới sau khi phiên trình duyệt hoạt động",
            "Bỏ qua video hoặc dừng danh sách nếu YouTube vẫn từ chối",
        ),
    ),
    "http_403": FriendlyError(
        "ERROR",
        "YouTube đang chặn luồng tải video",
        "YouTube tạm thời chặn lượt tải này. Thường do tải quá nhanh, tải quá nhiều, cookies yếu/hết hạn, hoặc thiếu runtime hỗ trợ.",
        (
            "Bật cookies.txt",
            "Đợi vài phút rồi thử lại",
            "Giảm số video tải liên tục",
            "Cập nhật yt-dlp.exe",
            "Nếu vẫn lỗi, thêm deno.exe cạnh yt-dlp.exe",
        ),
    ),
    "missing_js_runtime": FriendlyError(
        "WARNING",
        "Thiếu runtime hỗ trợ YouTube",
        "Một số video YouTube cần runtime JavaScript để yt-dlp xử lý xác thực/challenge.",
        (
            "Tải deno.exe",
            "Đặt deno.exe cùng thư mục với yt-dlp.exe",
            "Thử tải lại",
        ),
    ),
    "network": FriendlyError(
        "ERROR",
        "Lỗi mạng khi tải video",
        "Kết nối mạng không ổn định hoặc YouTube phản hồi chậm.",
        (
            "Kiểm tra mạng",
            "Thử tải lại sau vài phút",
            "Giảm số video tải liên tục",
            "Nếu dùng VPN/proxy, thử tắt hoặc đổi mạng",
        ),
    ),
    "invalid_api_key": FriendlyError(
        "ERROR",
        "API Key không hợp lệ",
        "API key sai, bị xoá, bị hạn chế sai API, hoặc chưa bật YouTube Data API v3.",
        (
            "Kiểm tra lại API key",
            "Bật YouTube Data API v3 trong Google Cloud",
            r"Thêm API key mới vào data\api key.txt",
        ),
    ),
    "api_key_file_error": FriendlyError(
        "ERROR",
        "Không thể đọc tệp API key. Hãy kiểm tra quyền truy cập và bảo đảm tệp được lưu bằng UTF-8.",
        "Tệp API key không thể được đọc an toàn.",
        (
            "Kiểm tra quyền truy cập tệp",
            "Lưu lại tệp bằng mã hóa UTF-8",
        ),
    ),
    "api_quota": FriendlyError(
        "ERROR",
        "API Key đã hết quota",
        "API key này đã dùng hết giới hạn trong ngày.",
        (
            "Đợi quota reset",
            r"Hoặc thêm API key khác vào data\api key.txt",
            "Tool sẽ thử key khác nếu có",
        ),
    ),
    "cannot_resolve_channel": FriendlyError(
        "ERROR",
        "Không tìm thấy kênh YouTube",
        "Link kênh sai, handle sai, hoặc dạng URL này chưa được hỗ trợ.",
        (
            "Kiểm tra lại link kênh",
            "Dùng dạng /channel/UC...",
            "Hoặc dùng @handle chính xác của kênh",
        ),
    ),
    "missing_ytdlp": FriendlyError(
        "ERROR",
        "Thiếu yt-dlp.exe",
        "Tool không tìm thấy yt-dlp.exe trong thư mục chương trình.",
        (
            r"Đặt yt-dlp.exe trong data\bin của thư mục portable",
            "Sau đó mở lại tool",
        ),
    ),
    "no_save_folder": FriendlyError(
        "ERROR",
        "Chưa chọn thư mục lưu",
        "Tool chưa biết cần lưu video vào thư mục nào.",
        (
            'Bấm "Chọn thư mục"',
            "Chọn một thư mục bạn có quyền ghi file",
            "Sau đó tải lại",
        ),
    ),
    "no_selected_videos": FriendlyError(
        "ERROR",
        "Chưa chọn video để tải",
        "Bạn chưa đánh dấu video nào trong danh sách.",
        (
            "Tick vào video cần tải",
            "Sau đó bấm tải lại",
        ),
    ),
    "no_videos_loaded": FriendlyError(
        "ERROR",
        "Chưa tải danh sách video",
        "Tool chưa có danh sách video để thao tác.",
        (
            "Nhập link kênh YouTube",
            'Bấm "Lấy danh sách Video"',
            "Sau đó chọn video cần tải",
        ),
    ),
    "invalid_speed_limit": FriendlyError(
        "ERROR",
        "Giới hạn tốc độ không hợp lệ",
        "Ô giới hạn tải chỉ nhận số MB/s, ví dụ 5 hoặc 1.5.",
        (
            "Nhập số dương, ví dụ 5",
            "Để trống hoặc nhập 0 nếu không muốn giới hạn tốc độ",
        ),
    ),
    "missing_ffmpeg": FriendlyError(
        "ERROR",
        "Thiếu ffmpeg.exe",
        "Tool cần ffmpeg để ghép video và audio hoặc convert thumbnail.",
        (
            r"Đặt ffmpeg.exe trong data\bin của thư mục portable",
            "Sau đó tải lại video",
        ),
    ),
    "premiere_safe_mp4": FriendlyError(
        "ERROR",
        "Không có bản MP4 H.264/AAC phù hợp cho Premiere",
        "Video này không có định dạng MP4 H.264/AAC hợp lệ ở 1080p trở xuống, hoặc file tải về không đạt chuẩn Premiere-safe.",
        (
            "Bỏ qua video này",
            "Cập nhật yt-dlp.exe rồi thử lại",
            "Nếu vẫn lỗi, tải thủ công bằng chế độ khác ngoài tool",
        ),
    ),
    "sqlite_state": FriendlyError(
        "ERROR",
        "Không thể mở cơ sở dữ liệu SQLite",
        "Tool không thể đọc hoặc ghi file trạng thái tải xuống.",
        (
            "Đóng các bản tool khác nếu đang mở",
            "Kiểm tra quyền ghi trong thư mục data",
            "Giữ lại file data\\download_state.sqlite3 nếu cần bảo toàn lịch sử tải",
        ),
    ),
    "audio_failed": FriendlyError(
        "ERROR",
        "Không thể trích xuất MP3",
        "yt-dlp hoặc ffmpeg không tạo được file MP3 hoàn chỉnh.",
        (
            r"Kiểm tra ffmpeg.exe nằm trong data\bin của thư mục portable",
            "Cập nhật yt-dlp.exe nếu file quá cũ",
            "Thử tải lại video sau vài phút",
        ),
    ),
    "ffmpeg_invalid_input": FriendlyError(
        "ERROR",
        "File MP4 nguồn không hợp lệ để trích xuất MP3",
        "File MP4 đã tải có thể chưa hoàn chỉnh, bị hỏng hoặc FFmpeg không đọc được.",
        (
            "Tải lại video để tạo file MP4 nguồn mới",
            "Kiểm tra file MP4 có phát được bằng trình phát video hay không",
            "Nếu lỗi lặp lại, cập nhật ffmpeg.exe rồi thử lại",
        ),
    ),
    "ffmpeg_no_audio": FriendlyError(
        "ERROR",
        "Video không có luồng âm thanh phù hợp",
        "FFmpeg không tìm thấy luồng âm thanh có thể trích xuất từ file MP4 nguồn.",
        (
            "Kiểm tra video gốc có âm thanh hay không",
            "Thử tải lại video bằng chế độ có MP3",
            "Nếu video là bản không có tiếng, bỏ qua phần MP3",
        ),
    ),
    "ffmpeg_encoder_unavailable": FriendlyError(
        "ERROR",
        "FFmpeg thiếu bộ mã hóa MP3",
        "Bản ffmpeg.exe hiện tại không có bộ mã hóa libmp3lame để tạo file MP3.",
        (
            "Thay ffmpeg.exe bằng bản đầy đủ có libmp3lame",
            r"Đặt ffmpeg.exe mới trong data\bin của thư mục portable",
            "Sau đó tải lại phần MP3",
        ),
    ),
    "disk_full": FriendlyError(
        "ERROR",
        "Ổ đĩa không còn đủ dung lượng",
        "Ổ lưu file hoặc thư mục tạm không còn đủ dung lượng để ghi file tải xuống.",
        (
            "Giải phóng dung lượng ổ đĩa đang lưu video",
            "Chọn thư mục lưu trên ổ còn nhiều dung lượng hơn",
            "Sau đó tải lại phần bị lỗi",
        ),
    ),
    "path_too_long": FriendlyError(
        "ERROR",
        "Tên file hoặc đường dẫn quá dài",
        "Tiêu đề video quá dài hoặc folder lưu nằm quá sâu.",
        (
            r"Chọn folder lưu ngắn hơn, ví dụ D:\YT",
            "Hoặc để tool tự rút gọn tên file",
            "Sau đó tải lại",
        ),
    ),
    "permission_denied": FriendlyError(
        "ERROR",
        "Không có quyền ghi file",
        "Windows không cho tool ghi vào thư mục đã chọn.",
        (
            r"Chọn thư mục khác như D:\Downloads",
            "Không lưu vào Program Files",
            "Đóng file video nếu đang mở bằng trình phát khác",
        ),
    ),
    "file_in_use": FriendlyError(
        "ERROR",
        "File đang được chương trình khác sử dụng",
        "File video/thumb đang mở hoặc bị chương trình khác giữ.",
        (
            "Đóng trình phát video/Premiere/File Explorer preview",
            "Sau đó tải lại",
        ),
    ),
    "file_exists": FriendlyError(
        "ERROR",
        "File đã tồn tại",
        "Windows báo file đích đã tồn tại hoặc không thể ghi đè khi tool hoàn tất tải.",
        (
            "Đóng chương trình đang mở file nếu có",
            "Thử tải lại sau vài giây",
            "Nếu lỗi lặp lại, đổi thư mục lưu hoặc đổi tên file cũ",
        ),
    ),
    "invalid_filename": FriendlyError(
        "ERROR",
        "Tên file không hợp lệ",
        "Windows từ chối tên file hoặc đường dẫn khi tool hoàn tất tải.",
        (
            "Chọn thư mục lưu ngắn hơn",
            "Kiểm tra tiêu đề video có ký tự lạ",
            "Thử tải lại",
        ),
    ),
    "stream_interrupted": FriendlyError(
        "ERROR",
        "Lỗi tải stream bị ngắt giữa chừng",
        "Kết nối tới YouTube bị ngắt hoặc YouTube trả thiếu dữ liệu trong lúc tải.",
        (
            "Thử tải lại video đó",
            "Kiểm tra mạng hoặc đổi mạng nếu lỗi lặp lại",
            "Giảm số video tải liên tục",
        ),
    ),
    "unknown_file_operation": FriendlyError(
        "ERROR",
        "Lỗi ghi file chưa xác định",
        "Tool gặp lỗi khi di chuyển hoặc ghi file sau khi tải.",
        (
            "Đóng các chương trình có thể đang mở file",
            "Kiểm tra ổ đĩa còn dung lượng và có quyền ghi",
            "Thử tải lại sau vài giây",
        ),
    ),
    "unsupported_codec": FriendlyError(
        "WARNING",
        "Video có thể dùng codec khó phát trên Windows",
        "Một số video YouTube dùng AV1, Windows Media Player có thể không phát hình.",
        (
            "Dùng VLC để mở",
            "Hoặc cài AV1 Video Extension",
            "Hoặc bật tùy chọn tải MP4/H.264 nếu tool hỗ trợ",
        ),
    ),
    "thumbnail_failed": FriendlyError(
        "WARNING",
        "Tải thumbnail thất bại",
        "YouTube không trả thumbnail hoặc convert ảnh lỗi.",
        (
            "Tool sẽ thử tải thumbnail bằng link API nếu có",
            'Nếu vẫn lỗi, video có thể tải xong nhưng trạng thái là "Thiếu thumbnail"',
        ),
    ),
    "interrupted": FriendlyError(
        "WARNING",
        "Tải video đã bị dừng",
        "Bạn đã dừng tải hoặc đóng app khi quá trình tải chưa xong.",
        (
            "Mở lại tool",
            "Chọn video đó và tải lại",
        ),
    ),
    "batch_blocked": FriendlyError(
        "WARNING",
        "YouTube đang chặn nhiều video liên tiếp",
        "Có thể bạn tải quá nhanh hoặc cookies không còn hợp lệ.",
        (
            "Đợi 5-10 phút, kiểm tra cookies.txt, rồi tải tiếp",
        ),
    ),
    "generic": FriendlyError(
        "ERROR",
        "Không thể hoàn tất thao tác",
        "Tool gặp lỗi trong quá trình xử lý.",
        (
            "Thử lại sau vài phút",
            "Nếu lỗi lặp lại, kiểm tra chi tiết kỹ thuật bên dưới",
        ),
    ),
}
