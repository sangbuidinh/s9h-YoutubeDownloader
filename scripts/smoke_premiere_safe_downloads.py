import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, downloader, state_store
from core.download_modes import MODE_VIDEO_AUDIO_THUMB, MODE_VIDEO_THUMB, PART_AUDIO, PART_THUMB, PART_VIDEO
from core.downloader import DownloadOptions
from core.error_messages import classify_general_error, classify_ytdlp_error
from core.file_status import build_output_paths


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"


def main() -> int:
    _configure_stdio()
    _test_format_selector()
    _test_base_command_flags()
    _test_error_classifier()
    _test_thumbnail_url_first_and_jpeg_only()
    _test_validation_parser()
    _test_premiere_safe_ready_is_non_destructive()
    _test_fresh_download_validation_can_delete_invalid_output()
    _test_stream_interrupted_retry()
    _test_video_audio_mode_extracts_from_local_mp4()
    _test_video_failure_preserves_existing_thumb()
    _test_video_failure_preserves_existing_audio_and_thumb_in_combined_mode()
    _test_audio_failure_does_not_remove_thumb_or_video()
    _test_thumbnail_failure_does_not_remove_video_or_audio()
    _test_move_single_file_can_replace_invalid_existing_video()
    _test_staged_mp4_validation_precedes_promotion()
    _test_invalid_staged_mp4_preserves_existing_final()
    _test_existing_valid_final_mp4_skips_download()
    print("Premiere-safe download smoke tests passed")
    return 0


def _test_format_selector() -> None:
    selector = downloader.PREMIERE_SAFE_VIDEO_FORMAT
    lower = selector.lower()
    for required in ("height<=1080", "ext=mp4", "vcodec^=avc1", "ext=m4a", "acodec^=mp4a"):
        _assert(required in lower, f"selector missing {required}")
    for forbidden in ("best", "webm", "vp9", "av01", "opus"):
        _assert(forbidden not in lower, f"selector contains forbidden token {forbidden}")
    _assert("bv*[ext=mp4]+ba[ext=m4a]" not in selector, "selector contains broad MP4 fallback")
    _assert("b[ext=mp4]" not in selector, "selector contains broad best-MP4 fallback")


def _test_base_command_flags() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        runtime_paths = {
            "yt-dlp.exe": root / "yt-dlp.exe",
            "ffmpeg.exe": root / "ffmpeg.exe",
            "deno.exe": root / "deno.exe",
        }
        for path in runtime_paths.values():
            path.write_bytes(b"")
        cookies_path = root / "cookies.txt"
        cookies_path.write_text("# placeholder\n", encoding="utf-8")

        old_runtime_file = downloader.runtime_file
        try:
            downloader.runtime_file = lambda filename: runtime_paths.get(filename, root / filename)
            command = downloader._base_ytdlp_command(
                DownloadOptions(
                    base_folder=str(root),
                    channel_id=CHANNEL_ID,
                    channel_name=CHANNEL_NAME,
                    cookies_enabled=True,
                    cookies_path=str(cookies_path),
                    speed_limit="2M",
                    download_mode=MODE_VIDEO_AUDIO_THUMB,
                )
            )
        finally:
            downloader.runtime_file = old_runtime_file

    expected_options = {
        "--retries": "30",
        "--fragment-retries": "30",
        "--file-access-retries": "10",
        "--socket-timeout": "60",
        "--http-chunk-size": "1M",
        "-N": "4",
        "--limit-rate": "2M",
    }
    for option, value in expected_options.items():
        _assert(_option_value(command, option) == value, f"{option} was not preserved as {value}")
    _assert("--newline" in command, "--newline missing")
    _assert(downloader.YTDLP_COOKIES_OPTION not in command, "base command passed canonical cookies")
    _assert("--ffmpeg-location" in command, "--ffmpeg-location missing")
    _assert("--js-runtimes" in command, "Deno runtime flags missing")
    _assert(any(str(value).startswith("deno:") for value in command), "Deno executable path missing")


def _test_error_classifier() -> None:
    messages = (
        "ERROR: requested format is not available",
        "ERROR: requested format not available",
        "ERROR: no video formats found",
        "ERROR: no suitable formats",
        "premiere_safe_mp4_validation_failed: video codec is not H.264/AVC",
    )
    for message in messages:
        general = classify_general_error(message)
        ytdlp = classify_ytdlp_error(message)
        _assert("MP4 H.264/AAC" in general.title, f"general classifier missed: {message}")
        _assert("MP4 H.264/AAC" in ytdlp.title, f"yt-dlp classifier missed: {message}")


def _test_thumbnail_url_first_and_jpeg_only() -> None:
    _assert(downloader._is_jpeg_download("image/jpeg", b"\xff\xd8\xffabc"), "valid JPEG was rejected")
    _assert(not downloader._is_jpeg_download("image/webp", b"RIFFxxxxWEBP"), "WebP was accepted")
    _assert(not downloader._is_jpeg_download("image/png", b"\x89PNG\r\n\x1a\n"), "PNG was accepted")
    _assert(not downloader._is_jpeg_download("", b""), "empty thumbnail was accepted")

    calls = []
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        final_path = temp_path / "thumb.jpg"
        video = SimpleNamespace(video_id="thumb-video", thumbnail_url="https://example.test/thumb.webp")

        old_direct = downloader._download_thumbnail_from_url
        old_run = downloader._run_ytdlp_with_retries
        try:
            def direct_first(_url, _temp_path, _final_path, _log=None, _cancel_controller=None):
                calls.append("url")
                raise downloader.DownloadError("thumbnail download failed")

            def ytdlp_fallback(command, _options, _log, _cancel_controller=None, _cookie_retry_state=None):
                calls.append("yt-dlp")
                output_template = Path(command[command.index("-o") + 1])
                output_template.with_name(output_template.name.replace("%(ext)s", "jpg")).write_bytes(b"\xff\xd8\xffjpg")

            downloader._download_thumbnail_from_url = direct_first
            downloader._run_ytdlp_with_retries = ytdlp_fallback
            downloader._download_thumbnail(
                video,
                "thumb-video",
                temp_path,
                final_path,
                DownloadOptions(str(temp_path), CHANNEL_ID, CHANNEL_NAME),
                lambda _message: None,
            )
        finally:
            downloader._download_thumbnail_from_url = old_direct
            downloader._run_ytdlp_with_retries = old_run

        _assert(calls == ["url", "yt-dlp"], f"thumbnail order was wrong: {calls}")
        _assert(final_path.exists() and final_path.stat().st_size > 0, "thumbnail fallback did not create final jpg")


def _test_validation_parser() -> None:
    pass_1080 = """
Stream #0:0: Video: h264 (High) (avc1 / 0x31637661), yuv420p, 1920x1080
Stream #0:1: Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo
"""
    pass_720 = """
codec_type=video|codec_name=h264|codec_tag_string=avc1|width=1280|height=720
codec_type=audio|codec_name=aac|codec_tag_string=mp4a
"""
    fail_cases = {
        "2160p": """
Stream #0:0: Video: h264 (High) (avc1 / 0x31637661), yuv420p, 3840x2160
Stream #0:1: Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo
""",
        "av1": """
Stream #0:0: Video: av1 (Main) (av01 / 0x31307661), yuv420p, 1920x1080
Stream #0:1: Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo
""",
        "vp9_opus": """
Stream #0:0: Video: vp9 (Profile 0), yuv420p, 1280x720
Stream #0:1: Audio: opus, 48000 Hz, stereo
""",
        "h264_opus": """
Stream #0:0: Video: h264 (High) (avc1 / 0x31637661), yuv420p, 1280x720
Stream #0:1: Audio: opus, 48000 Hz, stereo
""",
        "no_audio": """
Stream #0:0: Video: h264 (High) (avc1 / 0x31637661), yuv420p, 1280x720
""",
    }
    _assert(downloader._parse_premiere_safe_probe_output(pass_1080)[0], "H.264/AAC 1080p did not pass")
    _assert(downloader._parse_premiere_safe_probe_output(pass_720)[0], "H.264/AAC 720p did not pass")
    for name, output in fail_cases.items():
        _assert(not downloader._parse_premiere_safe_probe_output(output)[0], f"{name} unexpectedly passed")


def _test_premiere_safe_ready_is_non_destructive() -> None:
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "invalid.mp4"
        path.write_bytes(b"not a real h264 aac mp4")

        old_probe = downloader._probe_media_with_ffmpeg
        try:
            downloader._probe_media_with_ffmpeg = lambda _path: """
Stream #0:0: Video: av1 (Main) (av01 / 0x31307661), yuv420p, 1920x1080
Stream #0:1: Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo
"""
            ready = downloader._premiere_safe_mp4_ready(path)
        finally:
            downloader._probe_media_with_ffmpeg = old_probe

        _assert(not ready, "invalid MP4 was reported as ready")
        _assert(path.exists(), "readiness check deleted an existing invalid MP4")


def _test_fresh_download_validation_can_delete_invalid_output() -> None:
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "invalid_download.mp4"
        path.write_bytes(b"invalid current run output")

        old_probe = downloader._probe_media_with_ffmpeg
        try:
            downloader._probe_media_with_ffmpeg = lambda _path: """
Stream #0:0: Video: vp9 (Profile 0), yuv420p, 1280x720
Stream #0:1: Audio: opus, 48000 Hz, stereo
"""
            try:
                downloader._validate_premiere_safe_mp4(path, delete_invalid=True)
            except downloader.DownloadError:
                pass
            else:
                raise AssertionError("invalid MP4 validation unexpectedly passed")
        finally:
            downloader._probe_media_with_ffmpeg = old_probe

        _assert(not path.exists(), "destructive validation did not delete invalid fresh MP4")


def _test_stream_interrupted_retry() -> None:
    calls = []
    old_run = downloader._run_ytdlp
    old_sleep = downloader._sleep_with_cancel
    try:
        def run_once_then_pass(command, _cancel_controller=None):
            calls.append(command)
            if len(calls) == 1:
                raise downloader.YtdlpExecutionError(
                    1,
                    "stream interrupted",
                    [],
                    combined_output="Got error: 100 bytes read, 200 more expected",
                )
            return ""

        downloader._run_ytdlp = run_once_then_pass
        downloader._sleep_with_cancel = lambda _seconds, _cancel_controller: None
        command = ["yt-dlp", "--http-chunk-size", "1M", "-o", "x", "https://example.test/video"]
        downloader._run_ytdlp_with_retries(
            command,
            DownloadOptions(".", CHANNEL_ID, CHANNEL_NAME),
            lambda _message: None,
        )
    finally:
        downloader._run_ytdlp = old_run
        downloader._sleep_with_cancel = old_sleep

    _assert(len(calls) == 2, f"stream retry count was wrong: {len(calls)}")
    retry_command = calls[1]
    _assert(retry_command.count("--http-chunk-size") == 1, "retry duplicated --http-chunk-size")
    _assert(_option_value(retry_command, "--http-chunk-size") == "512K", "retry chunk size was not 512K")
    _assert("--no-continue" in retry_command, "retry did not add --no-continue")

    calls.clear()
    old_run = downloader._run_ytdlp
    old_sleep = downloader._sleep_with_cancel
    try:
        def bot_check(command, _cancel_controller=None):
            calls.append(command)
            raise downloader.YtdlpExecutionError(
                1,
                "bot check",
                [],
                bot_check=True,
                combined_output="Sign in to confirm you are not a bot. 100 bytes read, 200 more expected",
            )

        downloader._run_ytdlp = bot_check
        downloader._sleep_with_cancel = lambda _seconds, _cancel_controller: None
        try:
            downloader._run_ytdlp_with_retries(
                ["yt-dlp", "--http-chunk-size", "1M", "https://example.test/video"],
                DownloadOptions(".", CHANNEL_ID, CHANNEL_NAME, cookies_enabled=False),
                lambda _message: None,
            )
        except downloader.DownloadCancelled:
            pass
        else:
            raise AssertionError("bot-check safe-stop was swallowed")
    finally:
        downloader._run_ytdlp = old_run
        downloader._sleep_with_cancel = old_sleep
    _assert(len(calls) == 1, "stream retry overrode bot-check handling")


def _test_video_audio_mode_extracts_from_local_mp4() -> None:
    with TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir) / "data"
        db_path = data_dir / "download_state.sqlite3"
        video = _video("combo-video")
        paths = build_output_paths(data_dir, CHANNEL_NAME, video.sanitized_filename_base)
        _seed_video_and_thumb_downloaded(db_path, video, paths, data_dir)
        calls = []

        with _patched_db_file(db_path):
            old_validate_environment = downloader.validate_download_environment
            old_validate_mp4 = downloader._validate_premiere_safe_mp4
            old_download_video = downloader._download_video
            old_download_audio = downloader._download_audio
            old_extract_mp3 = downloader._extract_mp3_from_video
            try:
                downloader.validate_download_environment = lambda _options: None

                def validate_mp4(path, _log=None, delete_invalid: bool = True):
                    if not Path(path).exists():
                        raise downloader.DownloadError("premiere_safe_mp4_validation_failed: file does not exist")

                def download_video(
                    _video_id,
                    _stem,
                    _temp_dir,
                    final_path,
                    _options,
                    _log,
                    _cancel_controller=None,
                    _cookie_retry_state=None,
                ):
                    calls.append("video")
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    final_path.write_bytes(b"mp4")
                    validate_mp4(final_path)

                def download_audio(*_args, **_kwargs):
                    calls.append("yt-dlp-audio")
                    raise AssertionError("yt-dlp audio extraction should not run in combined mode")

                def extract_mp3(source_video_path, _temp_dir, final_audio_path, _log=None, _cancel_controller=None):
                    calls.append(("extract", Path(source_video_path)))
                    _assert(Path(source_video_path) == paths.video_path, "MP3 extraction used wrong source video")
                    final_audio_path.parent.mkdir(parents=True, exist_ok=True)
                    final_audio_path.write_bytes(b"mp3")

                downloader._validate_premiere_safe_mp4 = validate_mp4
                downloader._download_video = download_video
                downloader._download_audio = download_audio
                downloader._extract_mp3_from_video = extract_mp3
                downloader.download_items(
                    [video],
                    DownloadOptions(
                        base_folder=str(data_dir),
                        channel_id=CHANNEL_ID,
                        channel_name=CHANNEL_NAME,
                        download_mode=MODE_VIDEO_AUDIO_THUMB,
                    ),
                    lambda _message: None,
                    lambda _video: None,
                )
            finally:
                downloader.validate_download_environment = old_validate_environment
                downloader._validate_premiere_safe_mp4 = old_validate_mp4
                downloader._download_video = old_download_video
                downloader._download_audio = old_download_audio
                downloader._extract_mp3_from_video = old_extract_mp3

    _assert("video" in calls, "missing MP4 was not re-downloaded for audio extraction")
    _assert(any(call[0] == "extract" for call in calls if isinstance(call, tuple)), "local MP4 extraction did not run")
    _assert("yt-dlp-audio" not in calls, "combined mode called yt-dlp audio extraction")


def _test_video_failure_preserves_existing_thumb() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "data" / "download_state.sqlite3"
        video = _video("fail-video-thumb")
        paths = build_output_paths(root, CHANNEL_NAME, video.sanitized_filename_base)
        paths.thumb_path.parent.mkdir(parents=True, exist_ok=True)
        paths.thumb_path.write_bytes(b"old thumb")
        calls = []

        with _patched_db_file(db_path):
            old_validate_environment = downloader.validate_download_environment
            old_download_video = downloader._download_video
            old_download_thumbnail = downloader._download_thumbnail
            try:
                downloader.validate_download_environment = lambda _options: None

                def fail_video(*_args, **_kwargs):
                    calls.append("video")
                    raise downloader.DownloadError("premiere_safe_mp4_validation_failed: video codec is not H.264/AVC")

                def thumb_should_not_run(*_args, **_kwargs):
                    calls.append("thumb")
                    raise AssertionError("thumbnail should not run after video failure")

                downloader._download_video = fail_video
                downloader._download_thumbnail = thumb_should_not_run
                downloader.download_items(
                    [video],
                    DownloadOptions(
                        base_folder=str(root),
                        channel_id=CHANNEL_ID,
                        channel_name=CHANNEL_NAME,
                        download_mode=MODE_VIDEO_THUMB,
                    ),
                    lambda _message: None,
                    lambda _video: None,
                )
                entry = db_store.get_video_entry(CHANNEL_ID, video.video_id, save_base_folder=str(root))
            finally:
                downloader.validate_download_environment = old_validate_environment
                downloader._download_video = old_download_video
                downloader._download_thumbnail = old_download_thumbnail

        _assert("video" in calls, "video download did not run")
        _assert("thumb" not in calls, "thumbnail ran after video failure")
        _assert(paths.thumb_path.exists(), "existing thumbnail was removed after video failure")
        _assert(paths.thumb_path.read_bytes() == b"old thumb", "existing thumbnail content changed after video failure")
        _assert(
            state_store.part_status_from_entry(entry, PART_THUMB) != state_store.STATUS_ERROR,
            "thumb state was marked error after unrelated video failure",
        )


def _test_video_failure_preserves_existing_audio_and_thumb_in_combined_mode() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "data" / "download_state.sqlite3"
        video = _video("fail-video-combo")
        paths = build_output_paths(root, CHANNEL_NAME, video.sanitized_filename_base)
        paths.audio_path.parent.mkdir(parents=True, exist_ok=True)
        paths.thumb_path.parent.mkdir(parents=True, exist_ok=True)
        paths.audio_path.write_bytes(b"old mp3")
        paths.thumb_path.write_bytes(b"old thumb")

        with _patched_db_file(db_path):
            old_validate_environment = downloader.validate_download_environment
            old_download_video = downloader._download_video
            old_extract_mp3 = downloader._extract_mp3_from_video
            old_download_thumbnail = downloader._download_thumbnail
            try:
                downloader.validate_download_environment = lambda _options: None

                def fail_video(*_args, **_kwargs):
                    raise downloader.DownloadError("premiere_safe_mp4_validation_failed: no suitable formats")

                def audio_should_not_run(*_args, **_kwargs):
                    raise AssertionError("audio extraction should not run after video failure")

                def thumb_should_not_run(*_args, **_kwargs):
                    raise AssertionError("thumbnail should not run after video failure")

                downloader._download_video = fail_video
                downloader._extract_mp3_from_video = audio_should_not_run
                downloader._download_thumbnail = thumb_should_not_run
                downloader.download_items(
                    [video],
                    DownloadOptions(
                        base_folder=str(root),
                        channel_id=CHANNEL_ID,
                        channel_name=CHANNEL_NAME,
                        download_mode=MODE_VIDEO_AUDIO_THUMB,
                    ),
                    lambda _message: None,
                    lambda _video: None,
                )
                entry = db_store.get_video_entry(CHANNEL_ID, video.video_id, save_base_folder=str(root))
            finally:
                downloader.validate_download_environment = old_validate_environment
                downloader._download_video = old_download_video
                downloader._extract_mp3_from_video = old_extract_mp3
                downloader._download_thumbnail = old_download_thumbnail

        _assert(paths.audio_path.exists(), "existing MP3 was removed after video failure")
        _assert(paths.thumb_path.exists(), "existing thumbnail was removed after video failure")
        _assert(paths.audio_path.read_bytes() == b"old mp3", "existing MP3 content changed after video failure")
        _assert(paths.thumb_path.read_bytes() == b"old thumb", "existing thumbnail content changed after video failure")
        _assert(
            state_store.part_status_from_entry(entry, PART_AUDIO) != state_store.STATUS_ERROR,
            "audio state was marked error after unrelated video failure",
        )
        _assert(
            state_store.part_status_from_entry(entry, PART_THUMB) != state_store.STATUS_ERROR,
            "thumb state was marked error after unrelated video failure",
        )


def _test_audio_failure_does_not_remove_thumb_or_video() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "data" / "download_state.sqlite3"
        video = _video("fail-audio-only")
        paths = build_output_paths(root, CHANNEL_NAME, video.sanitized_filename_base)
        paths.video_path.parent.mkdir(parents=True, exist_ok=True)
        paths.thumb_path.parent.mkdir(parents=True, exist_ok=True)
        paths.video_path.write_bytes(b"existing video")
        paths.thumb_path.write_bytes(b"existing thumb")
        _seed_video_and_thumb_downloaded(db_path, video, paths, root)

        with _patched_db_file(db_path):
            old_validate_environment = downloader.validate_download_environment
            old_premiere_ready = downloader._premiere_safe_mp4_ready
            old_extract_mp3 = downloader._extract_mp3_from_video
            old_download_thumbnail = downloader._download_thumbnail
            try:
                downloader.validate_download_environment = lambda _options: None
                downloader._premiere_safe_mp4_ready = lambda _path: True

                def fail_audio(*_args, **_kwargs):
                    raise downloader.DownloadError("audio extraction failed")

                def thumb_should_not_run(*_args, **_kwargs):
                    raise AssertionError("thumbnail should not run after audio failure in this test")

                downloader._extract_mp3_from_video = fail_audio
                downloader._download_thumbnail = thumb_should_not_run
                downloader.download_items(
                    [video],
                    DownloadOptions(
                        base_folder=str(root),
                        channel_id=CHANNEL_ID,
                        channel_name=CHANNEL_NAME,
                        download_mode=MODE_VIDEO_AUDIO_THUMB,
                    ),
                    lambda _message: None,
                    lambda _video: None,
                )
            finally:
                downloader.validate_download_environment = old_validate_environment
                downloader._premiere_safe_mp4_ready = old_premiere_ready
                downloader._extract_mp3_from_video = old_extract_mp3
                downloader._download_thumbnail = old_download_thumbnail

        _assert(paths.video_path.exists(), "audio failure removed video unexpectedly")
        _assert(paths.thumb_path.exists(), "audio failure removed thumbnail unexpectedly")


def _test_thumbnail_failure_does_not_remove_video_or_audio() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "data" / "download_state.sqlite3"
        video = _video("fail-thumb-only")
        paths = build_output_paths(root, CHANNEL_NAME, video.sanitized_filename_base)
        paths.video_path.parent.mkdir(parents=True, exist_ok=True)
        paths.audio_path.parent.mkdir(parents=True, exist_ok=True)
        paths.video_path.write_bytes(b"existing video")
        paths.audio_path.write_bytes(b"existing audio")
        _seed_downloaded_parts(db_path, video, paths, root, (PART_VIDEO, PART_AUDIO))

        with _patched_db_file(db_path):
            old_validate_environment = downloader.validate_download_environment
            old_download_thumbnail = downloader._download_thumbnail
            try:
                downloader.validate_download_environment = lambda _options: None

                def fail_thumbnail(*_args, **_kwargs):
                    raise downloader.DownloadError("thumbnail download failed")

                downloader._download_thumbnail = fail_thumbnail
                downloader.download_items(
                    [video],
                    DownloadOptions(
                        base_folder=str(root),
                        channel_id=CHANNEL_ID,
                        channel_name=CHANNEL_NAME,
                        download_mode=MODE_VIDEO_AUDIO_THUMB,
                    ),
                    lambda _message: None,
                    lambda _video: None,
                )
            finally:
                downloader.validate_download_environment = old_validate_environment
                downloader._download_thumbnail = old_download_thumbnail

        _assert(paths.video_path.exists(), "thumbnail failure removed video unexpectedly")
        _assert(paths.audio_path.exists(), "thumbnail failure removed audio unexpectedly")


def _test_move_single_file_can_replace_invalid_existing_video() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        temp_root = root / "temp"
        out_root = root / "out"
        temp_root.mkdir()
        out_root.mkdir()
        final_path = out_root / "video.mp4"
        final_path.write_bytes(b"old invalid mp4")
        new_path = temp_root / "downloaded.mp4"
        new_path.write_bytes(b"new mp4")

        downloader._move_single_file(
            temp_root,
            "*.mp4",
            final_path,
            log=lambda _message: None,
            replace_existing=True,
        )

        _assert(final_path.read_bytes() == b"new mp4", "fresh replacement did not overwrite invalid existing MP4")


def _test_staged_mp4_validation_precedes_promotion() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        staging = root / "stage"
        staging.mkdir()
        final_path = root / "out" / "video.mp4"
        final_path.parent.mkdir()
        final_path.write_bytes(b"old mp4")
        calls = []
        validations = []

        def run_ytdlp(command, _options, _log, _cancel_controller=None, _cookie_retry_state=None):
            calls.append("download")
            _output_path(command).write_bytes(b"new mp4")

        def validate(path, _log, delete_invalid, _cancel_controller):
            calls.append("validate")
            validations.append((path, delete_invalid))

        def promote(source, target, *_args, **_kwargs):
            calls.append("promote")
            downloader.os.replace(source, target)

        old_run = downloader._run_ytdlp_with_retries
        old_ready = downloader._premiere_safe_mp4_ready_for_download
        old_validate = downloader._validate_premiere_safe_mp4_for_download
        old_promote = downloader._atomic_promote_with_retry
        try:
            downloader._run_ytdlp_with_retries = run_ytdlp
            downloader._premiere_safe_mp4_ready_for_download = lambda _path, _controller: False
            downloader._validate_premiere_safe_mp4_for_download = validate
            downloader._atomic_promote_with_retry = promote
            downloader._download_video(
                "video-atomic",
                "video-atomic",
                staging,
                final_path,
                DownloadOptions(str(root), CHANNEL_ID, CHANNEL_NAME),
                lambda _message: None,
            )
        finally:
            downloader._run_ytdlp_with_retries = old_run
            downloader._premiere_safe_mp4_ready_for_download = old_ready
            downloader._validate_premiere_safe_mp4_for_download = old_validate
            downloader._atomic_promote_with_retry = old_promote

        _assert(calls == ["download", "validate", "promote"], f"MP4 call order changed: {calls}")
        _assert(validations and validations[0][0].parent == staging, "MP4 validation did not use staged path")
        _assert(validations[0][0] != final_path, "MP4 validation was performed on final path before promotion")
        _assert(validations[0][1] is True, "staged invalid MP4 cleanup flag was not preserved")
        _assert(final_path.read_bytes() == b"new mp4", "valid staged MP4 was not promoted")


def _test_invalid_staged_mp4_preserves_existing_final() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        staging = root / "stage"
        staging.mkdir()
        final_path = root / "out" / "video.mp4"
        final_path.parent.mkdir()
        final_path.write_bytes(b"old mp4")
        promoted = []
        validated = []

        def run_ytdlp(command, _options, _log, _cancel_controller=None, _cookie_retry_state=None):
            _output_path(command).write_bytes(b"bad mp4")

        def validate(path, _log, _delete_invalid, _cancel_controller):
            validated.append(path)
            raise downloader.DownloadError("premiere_safe_mp4_validation_failed: invalid")

        old_run = downloader._run_ytdlp_with_retries
        old_ready = downloader._premiere_safe_mp4_ready_for_download
        old_validate = downloader._validate_premiere_safe_mp4_for_download
        old_promote = downloader._atomic_promote_with_retry
        try:
            downloader._run_ytdlp_with_retries = run_ytdlp
            downloader._premiere_safe_mp4_ready_for_download = lambda _path, _controller: False
            downloader._validate_premiere_safe_mp4_for_download = validate
            downloader._atomic_promote_with_retry = lambda *_args, **_kwargs: promoted.append("promote")
            try:
                downloader._download_video(
                    "video-invalid",
                    "video-invalid",
                    staging,
                    final_path,
                    DownloadOptions(str(root), CHANNEL_ID, CHANNEL_NAME),
                    lambda _message: None,
                )
            except downloader.DownloadError:
                pass
            else:
                raise AssertionError("invalid staged MP4 did not fail")
        finally:
            downloader._run_ytdlp_with_retries = old_run
            downloader._premiere_safe_mp4_ready_for_download = old_ready
            downloader._validate_premiere_safe_mp4_for_download = old_validate
            downloader._atomic_promote_with_retry = old_promote

        _assert(validated and validated[0].parent == staging, "invalid MP4 was not validated in staging")
        _assert(promoted == [], "invalid staged MP4 was promoted")
        _assert(final_path.read_bytes() == b"old mp4", "invalid staged MP4 replaced old final")


def _test_existing_valid_final_mp4_skips_download() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        staging = root / "stage"
        staging.mkdir()
        final_path = root / "out" / "video.mp4"
        final_path.parent.mkdir()
        final_path.write_bytes(b"valid mp4")
        calls = []

        old_run = downloader._run_ytdlp_with_retries
        old_ready = downloader._premiere_safe_mp4_ready_for_download
        try:
            downloader._run_ytdlp_with_retries = lambda *_args, **_kwargs: calls.append("download")
            downloader._premiere_safe_mp4_ready_for_download = lambda _path, _controller: True
            downloader._download_video(
                "video-skip",
                "video-skip",
                staging,
                final_path,
                DownloadOptions(str(root), CHANNEL_ID, CHANNEL_NAME),
                lambda _message: None,
            )
        finally:
            downloader._run_ytdlp_with_retries = old_run
            downloader._premiere_safe_mp4_ready_for_download = old_ready

        _assert(calls == [], "existing valid MP4 did not skip download")
        _assert(final_path.read_bytes() == b"valid mp4", "existing valid MP4 was changed")


def _seed_video_and_thumb_downloaded(db_path: Path, video, paths, save_base_folder: Path) -> None:
    _seed_downloaded_parts(db_path, video, paths, save_base_folder, (PART_VIDEO, PART_THUMB))


def _seed_downloaded_parts(db_path: Path, video, paths, save_base_folder: Path, parts: tuple[str, ...]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    part_paths = {
        PART_VIDEO: paths.video_path,
        PART_THUMB: paths.thumb_path,
        PART_AUDIO: paths.audio_path,
    }
    for part in parts:
        path = part_paths[part]
        db_store.update_video_part_state(
            CHANNEL_ID,
            video.video_id,
            part,
            filename=path.name,
            file_path=str(path),
            status=state_store.STATUS_DOWNLOADED,
            path=db_path,
            save_base_folder=str(save_base_folder),
            download_mode=MODE_VIDEO_AUDIO_THUMB,
            channel_name=CHANNEL_NAME,
            original_title=video.title,
            sanitized_filename_base=video.sanitized_filename_base,
            display_order_at_download=video.display_order,
        )


def _video(video_id: str):
    return SimpleNamespace(
        video_id=video_id,
        title=f"Video {video_id}",
        sanitized_filename_base=video_id,
        display_order=1,
        thumbnail_url="",
    )


@contextmanager
def _patched_db_file(db_path: Path):
    old_db_file = db_store.db_file
    old_state_db_file = state_store.db_file
    try:
        db_store.db_file = lambda: db_path
        state_store.db_file = lambda: db_path
        yield
    finally:
        db_store.db_file = old_db_file
        state_store.db_file = old_state_db_file


def _option_value(command: list[str], option: str) -> str | None:
    try:
        return command[command.index(option) + 1]
    except (ValueError, IndexError):
        return None


def _output_path(command: list[str]) -> Path:
    template = Path(command[command.index("-o") + 1])
    if "%(ext)s" not in template.name:
        return template
    return template.with_name(template.name.replace("%(ext)s", "mp4"))


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
