import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, downloader, state_store
from core.download_modes import MODE_VIDEO_THUMB
from core.downloader import (
    BatchDecision,
    COOKIE_SOURCE_BRIDGE,
    DownloadController,
    DownloadOptions,
    SkipCurrentVideo,
    SystemicBlockContext,
    YtdlpExecutionError,
    YtdlpFailureKind,
)
from ui import main_window


def main() -> int:
    _test_first_systemic_failure_pauses_and_skip_does_not_retry()
    _test_retry_requires_changed_cookie_source()
    _test_verified_retry_failure_pauses_without_retry_button()
    _test_cancel_wakes_paused_controller()
    _test_queued_block_after_stop_does_not_open_dialog()
    _test_cancel_while_dialog_returns_retry_uses_stop()
    _test_systemic_block_ui_logs_diagnostics()
    _test_stale_block_event_does_not_open_dialog()
    _test_no_controller_stops_batch_before_second_video()
    _test_controller_without_callback_stops_batch_before_second_video()
    _test_permanent_video_error_does_not_stop_batch()
    print("systemic batch pause smoke passed")
    return 0


def _test_first_systemic_failure_pauses_and_skip_does_not_retry() -> None:
    with TemporaryDirectory(prefix="systemic_pause_skip_") as temp_dir:
        cookie_path = _cookie(Path(temp_dir), "cookie=v1\n")
        contexts = []
        controller = DownloadController()

        def callback(context: SystemicBlockContext) -> None:
            contexts.append(context)
            controller.submit_systemic_decision(context.block_id, BatchDecision.SKIP_CURRENT.value)

        controller.systemic_block_callback = callback
        calls = []
        old_run_ytdlp = downloader._run_ytdlp
        try:
            downloader._run_ytdlp = lambda command, _cancel_controller=None: _raise_bot_check(calls, command)
            try:
                downloader._run_ytdlp_with_retries(
                    _command(),
                    _options(cookie_path),
                    lambda _message: None,
                    controller,
                )
            except SkipCurrentVideo:
                pass
            else:
                raise AssertionError("systemic skip did not stop current video")
        finally:
            downloader._run_ytdlp = old_run_ytdlp

        _assert(len(calls) == 1, f"systemic skip retried unexpectedly: {len(calls)}")
        _assert(len(contexts) == 1, f"pause context count was wrong: {len(contexts)}")
        _assert(contexts[0].failure_kind == YtdlpFailureKind.BOT_CHECK, "bot-check pause kind was wrong")
        _assert(contexts[0].retry_allowed, "initial cookie-backed pause should allow retry")
        _assert(str(cookie_path) not in calls[0], "yt-dlp command received canonical cookie path")


def _test_retry_requires_changed_cookie_source() -> None:
    with TemporaryDirectory(prefix="systemic_pause_retry_") as temp_dir:
        cookie_path = _cookie(Path(temp_dir), "cookie=v1\n")
        contexts = []
        logs = []
        controller = DownloadController()

        def callback(context: SystemicBlockContext) -> None:
            contexts.append(context)
            if len(contexts) == 2:
                cookie_path.write_text("cookie=v2\n", encoding="utf-8")
            controller.submit_systemic_decision(context.block_id, BatchDecision.RETRY_CURRENT.value)

        controller.systemic_block_callback = callback
        calls = []
        old_run_ytdlp = downloader._run_ytdlp
        try:
            def fail_once_then_pass(command, _cancel_controller=None):
                calls.append(command)
                if len(calls) == 1:
                    raise YtdlpExecutionError(
                        1,
                        "bot check",
                        ["Sign in to confirm you're not a bot"],
                        bot_check=True,
                        combined_output="Sign in to confirm you're not a bot",
                    )
                return ""

            downloader._run_ytdlp = fail_once_then_pass
            downloader._run_ytdlp_with_retries(_command(), _options(cookie_path), logs.append, controller)
        finally:
            downloader._run_ytdlp = old_run_ytdlp

        _assert(len(contexts) == 2, f"unchanged retry did not re-pause: {len(contexts)}")
        _assert(len(calls) == 2, f"changed cookie retry did not run exactly once: {len(calls)}")
        _assert(any("has not changed" in message for message in logs), "unchanged retry was not logged")


def _test_verified_retry_failure_pauses_without_retry_button() -> None:
    with TemporaryDirectory(prefix="systemic_pause_retry_failed_") as temp_dir:
        cookie_path = _cookie(Path(temp_dir), "cookie=v1\n")
        contexts = []
        controller = DownloadController()

        def callback(context: SystemicBlockContext) -> None:
            contexts.append(context)
            if len(contexts) == 1:
                cookie_path.write_text("cookie=v2\n", encoding="utf-8")
                controller.submit_systemic_decision(context.block_id, BatchDecision.RETRY_CURRENT.value)
            else:
                controller.submit_systemic_decision(context.block_id, BatchDecision.SKIP_CURRENT.value)

        controller.systemic_block_callback = callback
        calls = []
        old_run_ytdlp = downloader._run_ytdlp
        try:
            downloader._run_ytdlp = lambda command, _cancel_controller=None: _raise_bot_check(calls, command)
            try:
                downloader._run_ytdlp_with_retries(
                    _command(),
                    _options(cookie_path),
                    lambda _message: None,
                    controller,
                )
            except SkipCurrentVideo:
                pass
            else:
                raise AssertionError("failed verified retry did not pause for skip")
        finally:
            downloader._run_ytdlp = old_run_ytdlp

        _assert(len(calls) == 2, f"verified retry count was wrong: {len(calls)}")
        _assert(len(contexts) == 2, f"verified retry failure did not re-pause: {len(contexts)}")
        _assert(not contexts[1].retry_allowed, "second pause should not allow another retry")
        _assert(contexts[1].refreshed_retry_used, "second pause did not report used retry")


def _test_cancel_wakes_paused_controller() -> None:
    contexts = []
    controller = DownloadController(systemic_block_callback=lambda context: contexts.append(context))
    result = []
    context = SystemicBlockContext(
        block_id="cancel-test",
        failure_kind=YtdlpFailureKind.RATE_LIMIT,
        retry_allowed=False,
        reason="rate limited",
    )

    def wait_for_decision() -> None:
        result.append(controller.wait_for_systemic_decision(context))

    worker = threading.Thread(target=wait_for_decision, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not contexts:
        time.sleep(0.05)
    _assert(contexts, "controller did not publish pause context")
    controller.request_cancel()
    worker.join(timeout=5)
    _assert(not worker.is_alive(), "cancel did not wake paused controller")
    _assert(result == [BatchDecision.STOP_BATCH], f"cancel decision was wrong: {result}")


def _test_queued_block_after_stop_does_not_open_dialog() -> None:
    contexts = []
    controller = DownloadController(systemic_block_callback=lambda context: contexts.append(context))
    context = _context("queued-stop")
    result = []
    worker = threading.Thread(
        target=lambda: result.append(controller.wait_for_systemic_decision(context)),
        daemon=True,
    )
    worker.start()
    _wait_for(lambda: bool(contexts), "controller did not publish queued-stop context")

    window = _fake_window(controller)
    window.download_stop_requested = True
    controller.request_cancel()
    dialog_calls = []
    with _patched_dialog(lambda *_args, **_kwargs: dialog_calls.append("dialog")):
        window._handle_systemic_download_block(context)

    worker.join(timeout=5)
    _assert(not worker.is_alive(), "queued stop left worker waiting")
    _assert(not dialog_calls, "dialog opened after stop was already requested")
    _assert(result == [BatchDecision.STOP_BATCH], f"queued stop result was wrong: {result}")


def _test_cancel_while_dialog_returns_retry_uses_stop() -> None:
    with TemporaryDirectory(prefix="systemic_cancel_dialog_") as temp_dir:
        cookie_path = _cookie(Path(temp_dir), "cookie=v1\n")
        controller = DownloadController()
        window = _fake_window(controller)
        controller.systemic_block_callback = lambda context: window._handle_systemic_download_block(context)
        calls = []
        dialog_calls = []
        old_run_ytdlp = downloader._run_ytdlp
        try:
            def fake_dialog(*_args, **_kwargs):
                dialog_calls.append("dialog")
                controller.request_cancel()
                return BatchDecision.RETRY_CURRENT.value

            downloader._run_ytdlp = lambda command, _cancel_controller=None: _raise_bot_check(calls, command)
            with _patched_dialog(fake_dialog):
                try:
                    downloader._run_ytdlp_with_retries(
                        _command(),
                        _options(cookie_path),
                        lambda _message: None,
                        controller,
                    )
                except downloader.DownloadCancelled:
                    pass
                else:
                    raise AssertionError("cancel during dialog did not stop batch")
        finally:
            downloader._run_ytdlp = old_run_ytdlp

        _assert(dialog_calls == ["dialog"], f"dialog call count was wrong: {dialog_calls}")
        _assert(len(calls) == 1, f"retry was accepted after cancellation: {len(calls)}")


def _test_systemic_block_ui_logs_diagnostics() -> None:
    controller = DownloadController()
    window = _fake_window(controller)
    dialog_messages: list[str] = []

    def fake_dialog(*_args, **kwargs):
        dialog_messages.append(kwargs.get("message", ""))
        return BatchDecision.SKIP_CURRENT.value

    context = SystemicBlockContext(
        block_id="diagnostic-ui",
        failure_kind=YtdlpFailureKind.RATE_LIMIT,
        retry_allowed=False,
        reason="rate limited",
        part="video",
        stage="extract",
        exit_code=1,
        output_lines=("ERROR: HTTP Error 429: Too Many Requests",),
    )
    controller.systemic_block_callback = lambda queued_context: window._handle_systemic_download_block(queued_context)

    with _patched_dialog(fake_dialog):
        decision = controller.wait_for_systemic_decision(context)

    _assert(decision == BatchDecision.SKIP_CURRENT, f"diagnostic dialog decision was wrong: {decision}")
    logs = "\n".join(window.logs)
    _assert(
        "[YT-DLP PAUSE] part=video stage=extract exit_code=1 failure_kind=rate_limit_429" in logs,
        f"pause diagnostic log missing: {logs}",
    )
    _assert(
        "[YT-DLP PAUSE FATAL] ERROR: HTTP Error 429: Too Many Requests" in logs,
        f"pause fatal log missing: {logs}",
    )
    _assert(dialog_messages, "diagnostic dialog did not open")
    dialog = dialog_messages[0]
    for expected in (
        "Stage: extract",
        "Type: rate_limit_429",
        "Exit code: 1",
        "Fatal: ERROR: HTTP Error 429: Too Many Requests",
    ):
        _assert(expected in dialog, f"dialog missing {expected!r}: {dialog}")


def _test_stale_block_event_does_not_open_dialog() -> None:
    controller = DownloadController(systemic_block_callback=lambda _context: None)
    window = _fake_window(controller)
    context = _context("stale-event")
    dialog_calls = []
    with _patched_dialog(lambda *_args, **_kwargs: dialog_calls.append("dialog")):
        window._handle_systemic_download_block(context)
    _assert(not dialog_calls, "stale block event opened a dialog")
    _assert(
        not controller.submit_systemic_decision(context.block_id, BatchDecision.RETRY_CURRENT.value),
        "stale decision was accepted",
    )


def _test_no_controller_stops_batch_before_second_video() -> None:
    calls, logs = _run_two_video_batch(None, "bot")
    _assert(len(calls) == 1, f"no-controller systemic failure started next video: {len(calls)}")
    _assert(any("no pause callback" in message for message in logs), "no-controller stop was not logged")


def _test_controller_without_callback_stops_batch_before_second_video() -> None:
    controller = DownloadController()
    calls, logs = _run_two_video_batch(controller, "bot")
    _assert(controller.is_cancel_requested(), "controller without callback was not cancelled")
    _assert(len(calls) == 1, f"controller without callback started next video: {len(calls)}")
    _assert(any("no pause callback" in message for message in logs), "missing-callback stop was not logged")


def _test_permanent_video_error_does_not_stop_batch() -> None:
    contexts = []
    controller = DownloadController(systemic_block_callback=lambda context: contexts.append(context))
    calls, _logs = _run_two_video_batch(controller, "permanent")
    _assert(not controller.is_cancel_requested(), "permanent-video error cancelled the batch")
    _assert(len(calls) >= 2, f"permanent-video error stopped before next video: {len(calls)}")
    _assert(not contexts, "permanent-video error opened systemic callback")


def _raise_bot_check(calls: list, command: list[str]) -> None:
    calls.append(command)
    raise YtdlpExecutionError(
        1,
        "bot check",
        ["Sign in to confirm you're not a bot"],
        bot_check=True,
        combined_output="Sign in to confirm you're not a bot",
    )


def _run_two_video_batch(controller: DownloadController | None, failure: str) -> tuple[list[list[str]], list[str]]:
    calls: list[list[str]] = []
    logs: list[str] = []
    old_run_ytdlp = downloader._run_ytdlp
    old_validate = downloader.validate_download_environment
    old_summary = downloader._log_runtime_tool_summary
    try:
        def run_ytdlp(command, _cancel_controller=None):
            calls.append(command)
            if len(calls) == 1:
                if failure == "permanent":
                    raise YtdlpExecutionError(
                        1,
                        "private video",
                        ["Private video. Sign in if you've been granted access"],
                        combined_output="Private video. Sign in if you've been granted access",
                    )
                raise YtdlpExecutionError(
                    1,
                    "bot check",
                    ["Sign in to confirm you're not a bot"],
                    bot_check=True,
                    combined_output="Sign in to confirm you're not a bot",
                )
            return ""

        downloader._run_ytdlp = run_ytdlp
        downloader.validate_download_environment = lambda _options: None
        downloader._log_runtime_tool_summary = lambda _log: None
        with TemporaryDirectory(prefix="systemic_two_video_") as temp_dir:
            root = Path(temp_dir)
            with _patched_db_file(root / "data" / "download_state.sqlite3"):
                downloader.download_items(
                    [_video("first"), _video("second")],
                    DownloadOptions(
                        base_folder=str(root),
                        channel_id="channel",
                        channel_name="Channel",
                        download_mode=MODE_VIDEO_THUMB,
                        file_start_number=1,
                    ),
                    logs.append,
                    lambda _video_arg: None,
                    cancel_controller=controller,
                )
    finally:
        downloader._run_ytdlp = old_run_ytdlp
        downloader.validate_download_environment = old_validate
        downloader._log_runtime_tool_summary = old_summary
    return calls, logs


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


def _fake_window(controller: DownloadController):
    window = main_window.YouTubeDownloaderWindow.__new__(main_window.YouTubeDownloaderWindow)
    window.root = object()
    window.downloading = True
    window.download_stop_requested = False
    window.download_controller = controller
    window.progress_current_var = _Var()
    window.progress_detail_var = _Var()
    window.logs = []
    window._append_log = lambda message: window.logs.append(message)
    return window


class _Var:
    def __init__(self):
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class _patched_dialog:
    def __init__(self, replacement):
        self.replacement = replacement
        self.original = None

    def __enter__(self):
        self.original = main_window.show_app_dialog
        main_window.show_app_dialog = self.replacement
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        main_window.show_app_dialog = self.original


def _context(block_id: str) -> SystemicBlockContext:
    return SystemicBlockContext(
        block_id=block_id,
        failure_kind=YtdlpFailureKind.BOT_CHECK,
        retry_allowed=True,
        reason="bot check",
    )


def _wait_for(condition, message: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError(message)


def _video(video_id: str):
    return SimpleNamespace(
        video_id=video_id,
        title=f"Video {video_id}",
        sanitized_filename_base=video_id,
        display_order=1,
        thumbnail_url="",
        status="",
    )


def _command() -> list[str]:
    return ["yt-dlp", "--newline", "https://www.youtube.com/watch?v=pause123"]


def _cookie(root: Path, content: str) -> Path:
    path = root / "youtube_cookies.txt"
    path.write_text(content, encoding="utf-8")
    return path


def _options(cookie_path: Path) -> DownloadOptions:
    return DownloadOptions(
        base_folder=".",
        channel_id="channel",
        channel_name="Channel",
        cookies_enabled=True,
        cookie_source=COOKIE_SOURCE_BRIDGE,
        bridge_cookie_path=str(cookie_path),
    )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
