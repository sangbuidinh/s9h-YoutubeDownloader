import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import queue


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.downloader import DownloadController, DownloadOptions
from ui import main_window


def main() -> int:
    _test_runtime_summary_wrapper_passes_controller()
    _test_active_probe_registration()
    _test_cancel_hanging_version_probe()
    _test_no_next_probe_after_cancel()
    _test_cancel_before_probe_start()
    _test_cancel_after_registration()
    _test_normal_version_result()
    _test_nonzero_exit_is_nonfatal()
    _test_timeout_without_cancel_is_nonfatal()
    _test_exit_waits_for_hanging_version_probe()
    _test_normal_stop_keeps_app_open_during_version_probe()
    print("cancellable tool probe smoke passed")
    return 0


def _test_runtime_summary_wrapper_passes_controller() -> None:
    controller = DownloadController()
    seen = []
    old_summary = downloader._log_runtime_tool_summary
    try:
        def summary(options, _log, cancel_controller=None):
            seen.append((options, cancel_controller))

        downloader._log_runtime_tool_summary = summary
        with TemporaryDirectory(prefix="probe_summary_controller_") as temp_dir:
            options = DownloadOptions(str(Path(temp_dir)), "channel", "Channel", file_start_number=1)
            downloader._call_runtime_tool_summary(options, lambda _message: None, controller)
    finally:
        downloader._log_runtime_tool_summary = old_summary

    _assert(len(seen) == 1, f"summary call count was wrong: {seen}")
    _assert(seen[0][1] is controller, "download controller was not passed to runtime summary")


def _test_active_probe_registration() -> None:
    controller = DownloadController()
    result = []
    command = [
        sys.executable,
        "-c",
        "import time; time.sleep(0.4); print('2026.01.02')",
    ]

    worker = threading.Thread(
        target=lambda: result.append(
            downloader._get_command_version(command, cancel_controller=controller)
        ),
        daemon=False,
    )
    worker.start()
    process = _wait_for_current_process(controller)
    _assert(controller.has_active_process(), "controller did not report active probe")
    worker.join(timeout=5)

    _assert(not worker.is_alive(), "normal version probe did not finish")
    _assert(result == ["2026.01.02"], f"version result was wrong: {result}")
    _assert(process.poll() is not None, "probe process was still running")
    _assert(not controller.has_active_process(), "controller still reported active probe")
    _assert(controller.current_process is None, "probe process reference was not cleared")


def _test_cancel_hanging_version_probe() -> None:
    controller = DownloadController()
    result = []
    command = [sys.executable, "-c", "import time; time.sleep(30)"]

    def run_probe() -> None:
        try:
            downloader._get_command_version(command, cancel_controller=controller)
        except downloader.DownloadCancelled:
            result.append("cancelled")
        except BaseException as exc:
            result.append(f"{type(exc).__name__}: {exc}")
        else:
            result.append("completed")

    worker = threading.Thread(target=run_probe, daemon=False)
    worker.start()
    process = _wait_for_current_process(controller)

    start = time.monotonic()
    controller.request_cancel()
    worker.join(timeout=5)
    elapsed = time.monotonic() - start

    _assert(not worker.is_alive(), "cancelled version probe did not stop")
    _assert(elapsed < 3, f"probe cancellation was too slow: {elapsed:.2f}s")
    _assert(result == ["cancelled"], f"probe cancellation result was wrong: {result}")
    _assert(process.poll() is not None, "cancelled probe process was left running")
    _assert(not controller.has_active_process(), "controller still reported active process")
    _assert(controller.current_process is None, "cancelled probe process reference was not cleared")


def _test_no_next_probe_after_cancel() -> None:
    controller = DownloadController()
    calls = []
    old_probe = downloader._get_command_version
    try:
        def fake_probe(command, *, cancel_controller=None, timeout_seconds=10.0):
            calls.append(command)
            _assert(cancel_controller is controller, "summary did not pass controller to probe")
            controller.request_cancel()
            raise downloader.DownloadCancelled("download cancelled/interrupted")

        downloader._get_command_version = fake_probe
        try:
            downloader._log_runtime_tool_summary(
                DownloadOptions(".", "channel", "Channel"),
                lambda _message: None,
                controller,
            )
        except downloader.DownloadCancelled:
            pass
        else:
            raise AssertionError("runtime summary did not propagate cancellation")
    finally:
        downloader._get_command_version = old_probe

    _assert(len(calls) == 1, f"later probes ran after cancellation: {calls}")


def _test_cancel_before_probe_start() -> None:
    controller = DownloadController()
    controller.request_cancel()
    start = time.monotonic()
    try:
        downloader._get_command_version(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cancel_controller=controller,
        )
    except downloader.DownloadCancelled:
        pass
    else:
        raise AssertionError("pre-cancelled probe did not raise DownloadCancelled")

    elapsed = time.monotonic() - start
    _assert(elapsed < 1, f"pre-cancelled probe attempted to run: {elapsed:.2f}s")
    _assert(not controller.has_active_process(), "pre-cancelled controller reported active process")
    _assert(controller.current_process is None, "pre-cancelled controller retained a process")


def _test_cancel_after_registration() -> None:
    controller = _CancelAfterRegisterController()
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    start = time.monotonic()
    try:
        downloader._get_command_version(command, cancel_controller=controller)
    except downloader.DownloadCancelled:
        pass
    else:
        raise AssertionError("post-registration cancellation did not raise DownloadCancelled")

    elapsed = time.monotonic() - start
    _assert(elapsed < 3, f"post-registration cancellation was too slow: {elapsed:.2f}s")
    _assert(controller.registered_process is not None, "probe did not register a process")
    _assert(controller.registered_process.poll() is not None, "registered process was left running")
    _assert(not controller.has_active_process(), "controller still reported active process")
    _assert(controller.current_process is None, "registered process reference was not cleared")


def _test_normal_version_result() -> None:
    controller = DownloadController()
    version = downloader._get_command_version(
        [sys.executable, "-c", "print('2026.01.02')"],
        cancel_controller=controller,
    )
    _assert(version == "2026.01.02", f"normal version parse changed: {version!r}")
    _assert(not controller.has_active_process(), "controller not idle after normal version probe")
    _assert(controller.current_process is None, "normal version probe reference was not cleared")


def _test_nonzero_exit_is_nonfatal() -> None:
    controller = DownloadController()
    version = downloader._get_command_version(
        [
            sys.executable,
            "-c",
            "import sys; print('version error', file=sys.stderr); sys.exit(7)",
        ],
        cancel_controller=controller,
    )
    _assert(version == "version error", f"non-zero version parse changed: {version!r}")
    _assert(not controller.is_cancel_requested(), "non-zero version probe cancelled the batch")
    _assert(not controller.has_active_process(), "controller not idle after non-zero probe")
    _assert(controller.current_process is None, "non-zero probe reference was not cleared")


def _test_timeout_without_cancel_is_nonfatal() -> None:
    controller = _CapturingController()
    start = time.monotonic()
    version = downloader._get_command_version(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cancel_controller=controller,
        timeout_seconds=0.2,
    )
    elapsed = time.monotonic() - start

    _assert(version == "", f"timed-out probe returned a version: {version!r}")
    _assert(elapsed < 3, f"timed-out probe waited too long: {elapsed:.2f}s")
    _assert(not controller.is_cancel_requested(), "timeout requested cancellation")
    _assert(controller.registered_process is not None, "timeout probe did not register")
    _assert(controller.registered_process.poll() is not None, "timed-out child was left running")
    _assert(not controller.has_active_process(), "controller not idle after timeout")
    _assert(controller.current_process is None, "timeout probe reference was not cleared")


def _test_exit_waits_for_hanging_version_probe() -> None:
    controller = DownloadController()
    terminal_received = threading.Event()
    worker = _start_hanging_probe_worker(controller, terminal_received)
    window = _fake_window(worker, controller)
    process = _wait_for_current_process(controller)

    window._on_close()
    window._poll_shutdown_completion()
    _assert(window.root.destroy_count == 0, "root destroyed while version probe was active")

    worker.join(timeout=5)
    _assert(not worker.is_alive(), "exit did not stop hanging version probe")
    _assert(terminal_received.is_set(), "worker did not reach terminal path")
    window._download_terminal_received = True
    _assert(process.poll() is not None, "exit left probe process running")
    window._poll_shutdown_completion()

    _assert(window.root.destroy_count == 1, "root did not destroy after worker and controller idle")
    window._poll_shutdown_completion()
    _assert(window.root.destroy_count == 1, "root destroyed more than once")


def _test_normal_stop_keeps_app_open_during_version_probe() -> None:
    controller = DownloadController()
    terminal_received = threading.Event()
    worker = _start_hanging_probe_worker(controller, terminal_received)
    window = _fake_window(worker, controller)
    process = _wait_for_current_process(controller)

    window.stop_download()
    _assert(window.root.destroy_count == 0, "normal Stop destroyed root")
    _assert(not window.shutdown_in_progress, "normal Stop entered shutdown mode")
    _assert(not window.exit_after_download_stop, "normal Stop requested exit")

    worker.join(timeout=5)
    _assert(not worker.is_alive(), "normal Stop did not stop version probe")
    _assert(terminal_received.is_set(), "worker did not reach terminal path after Stop")
    window._download_terminal_received = True
    _assert(process.poll() is not None, "normal Stop left probe process running")
    window._poll_download_finish_completion()

    _assert(window.root.destroy_count == 0, "normal Stop closed the application")
    _assert(not window.downloading, "download state was not cleared after normal Stop")
    _assert(window.controls_locked and window.controls_locked[-1] is False, "controls did not unlock")


def _start_hanging_probe_worker(
    controller: DownloadController,
    terminal_received: threading.Event,
) -> threading.Thread:
    def run() -> None:
        try:
            downloader._get_command_version(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cancel_controller=controller,
            )
        except downloader.DownloadCancelled:
            pass
        finally:
            terminal_received.set()

    worker = threading.Thread(target=run, daemon=False)
    worker.start()
    return worker


def _wait_for_current_process(controller: DownloadController):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if controller.has_active_process():
            return controller.current_process
        time.sleep(0.05)
    raise AssertionError("probe did not register current process")


class _CapturingController(DownloadController):
    def __init__(self) -> None:
        super().__init__()
        self.registered_process = None

    def set_current_process(self, process) -> None:
        self.registered_process = process
        super().set_current_process(process)


class _CancelAfterRegisterController(_CapturingController):
    def set_current_process(self, process) -> None:
        super().set_current_process(process)
        self.request_cancel()


def _fake_window(worker, controller):
    window = main_window.YouTubeDownloaderWindow.__new__(main_window.YouTubeDownloaderWindow)
    window.root = _FakeRoot()
    window.events = queue.Queue()
    window.progress_queue = queue.Queue(maxsize=1)
    window.downloading = True
    window.download_worker = worker
    window.download_controller = controller
    window.download_stop_requested = False
    window.exit_after_download_stop = False
    window.close_requested = False
    window.cancel_download = False
    window.shutdown_in_progress = False
    window.shutdown_started_at = None
    window._shutdown_poll_after_id = None
    window._download_finish_poll_after_id = None
    window._root_destroyed = False
    window._shutdown_slow_warning_logged = False
    window._shutdown_cancel_reissued = False
    window._download_terminal_received = False
    window._download_terminal_outcome = ""
    window._download_terminal_message = ""
    window.progress_current_var = _Var()
    window.progress_detail_var = _Var()
    window.logs = []
    window.controls_locked = []
    window.progress_events = []
    window.confirm_calls = 0
    window._append_log = lambda message: window.logs.append(message)
    window._set_download_controls_locked = lambda locked: window.controls_locked.append(locked)
    window._update_stop_button_state = lambda: None
    window._update_cookies_state = lambda: None
    window._update_more_button_state = lambda: None
    window._update_download_button_text = lambda: None
    window._enqueue_progress_event = lambda event: window.progress_events.append(event)

    def confirm() -> bool:
        window.confirm_calls += 1
        return True

    window._confirm_exit_while_downloading = confirm
    return window


class _FakeRoot:
    def __init__(self) -> None:
        self.destroy_count = 0
        self.scheduled = []
        self.cancelled = []

    def after(self, delay_ms: int, callback):
        after_id = f"after-{len(self.scheduled) + 1}"
        self.scheduled.append((after_id, delay_ms, callback))
        return after_id

    def after_cancel(self, after_id) -> None:
        self.cancelled.append(after_id)

    def destroy(self) -> None:
        self.destroy_count += 1


class _Var:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
