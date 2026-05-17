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
        if cookies_enabled:
            return FRIENDLY_ERRORS["cookies_invalid"]
        return FRIENDLY_ERRORS["bot_check"]
    if http_403 or _contains_http_403(text):
        return FRIENDLY_ERRORS["http_403"]
    if missing_js_runtime or _contains_js_runtime(text):
        return FRIENDLY_ERRORS["missing_js_runtime"]
    return classify_general_error(text)


def classify_api_error(code: str, message: str = "") -> FriendlyError:
    haystack = f"{code} {message}".lower()
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
    if _contains_stream_interrupted(lower):
        return FRIENDLY_ERRORS["stream_interrupted"]
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
        "sign in to confirm" in lower
        or "not a bot" in lower
        or "use --cookies" in lower
        or "confirm you're not a bot" in lower
    )


def _contains_http_403(text: str) -> bool:
    lower = (text or "").lower()
    return "http error 403" in lower or "403: forbidden" in lower or "forbidden" in lower


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
    return "cookies file missing" in lower or "cookies.txt missing" in lower or "chưa chọn cookies" in lower


def _contains_missing_ytdlp(text: str) -> bool:
    lower = (text or "").lower()
    return "yt-dlp.exe missing" in lower or ("filenotfounderror" in lower and "yt-dlp" in lower)


def _contains_missing_ffmpeg(text: str) -> bool:
    lower = (text or "").lower()
    return "ffmpeg.exe missing" in lower or "ffmpeg not found" in lower


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
            "Thêm API key mới vào api key.txt",
        ),
    ),
    "api_quota": FriendlyError(
        "ERROR",
        "API Key đã hết quota",
        "API key này đã dùng hết giới hạn trong ngày.",
        (
            "Đợi quota reset",
            "Hoặc thêm API key khác vào api key.txt",
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
            "Đặt yt-dlp.exe cùng thư mục với file .exe của tool",
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
            "Đặt ffmpeg.exe cùng thư mục với file .exe của tool",
            "Sau đó tải lại video",
        ),
    ),
    "audio_failed": FriendlyError(
        "ERROR",
        "Không thể trích xuất MP3",
        "yt-dlp hoặc ffmpeg không tạo được file MP3 hoàn chỉnh.",
        (
            "Kiểm tra ffmpeg.exe nằm cùng thư mục với tool",
            "Cập nhật yt-dlp.exe nếu file quá cũ",
            "Thử tải lại video sau vài phút",
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
