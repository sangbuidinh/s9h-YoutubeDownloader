import queue
import sys
import threading
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.downloader import BatchDecision, DownloadController, SystemicBlockContext, YtdlpFailureKind
from ui import main_window


def main() -> int:
    _test_close_during_active_download_does_not_destroy_root()
    _test_shutdown_waits_for_worker_when_process_gone()
    _test_shutdown_waits_for_process_when_worker_gone()
    _test_shutdown_destroys_after_worker_and_process_idle()
    _test_normal_stop_keeps_root_open()
    _test_controls_unlock_only_after_worker_exit()
    _test_repeated_close_does_not_duplicate_shutdown()
    _test_terminal_event_before_thread_exit()
    _test_thread_exit_before_terminal_event()
    _test_error_terminal_event_during_shutdown()
    _test_exit_wakes_systemic_pause()
    _test_slow_shutdown_warns_once_without_forced_destroy()
    _test_destroy_root_once_and_periodic_callbacks_stop()
    print("safe shutdown smoke passed")
    return 0


def _test_close_during_active_download_does_not_destroy_root() -> None:
    controller = _FakeController(active_process=True)
    window = _fake_window(_FakeWorker(alive=True), controller)

    window._on_close()

    _assert(window.confirm_calls == 1, f"confirmation count was wrong: {window.confirm_calls}")
    _assert(window.shutdown_in_progress, "shutdown flag was not set")
    _assert(window.close_requested, "close flag was not set")
    _assert(window.exit_after_download_stop, "exit-after-stop flag was not set")
    _assert(window.download_stop_requested, "download stop flag was not set")
    _assert(controller.cancel_requests == 1, f"cancel request count was wrong: {controller.cancel_requests}")
    _assert(window.root.destroy_count == 0, "root was destroyed immediately")
    _assert(window._shutdown_poll_after_id is not None, "shutdown poll was not scheduled")


def _test_shutdown_waits_for_worker_when_process_gone() -> None:
    controller = _FakeController(active_process=False)
    window = _fake_window(_FakeWorker(alive=True), controller)
    window.shutdown_in_progress = True
    window._download_terminal_received = True

    window._poll_shutdown_completion()

    _assert(window.root.destroy_count == 0, "root destroyed while worker was alive")
    _assert(window._shutdown_poll_after_id is not None, "shutdown poll did not continue")


def _test_shutdown_waits_for_process_when_worker_gone() -> None:
    controller = _FakeController(active_process=True)
    window = _fake_window(_FakeWorker(alive=False), controller)
    window.shutdown_in_progress = True
    window._download_terminal_received = True

    window._poll_shutdown_completion()

    _assert(window.root.destroy_count == 0, "root destroyed while process was active")
    _assert(window.download_controller is controller, "active controller was cleared early")
    _assert(window._shutdown_poll_after_id is not None, "shutdown poll did not continue")


def _test_shutdown_destroys_after_worker_and_process_idle() -> None:
    window = _fake_window(_FakeWorker(alive=False), _FakeController(active_process=False))
    window.shutdown_in_progress = True
    window._download_terminal_received = True

    window._poll_shutdown_completion()
    window._poll_shutdown_completion()

    _assert(window.root.destroy_count == 1, f"root destroy count was wrong: {window.root.destroy_count}")
    _assert(window.download_worker is None, "worker reference was not cleared")
    _assert(window.download_controller is None, "controller reference was not cleared")


def _test_normal_stop_keeps_root_open() -> None:
    controller = _FakeController(active_process=True)
    window = _fake_window(_FakeWorker(alive=True), controller)

    window.stop_download()

    _assert(controller.cancel_requests == 1, "normal Stop did not request cancellation")
    _assert(not window.shutdown_in_progress, "normal Stop entered shutdown state")
    _assert(not window.exit_after_download_stop, "normal Stop set exit-after-stop")
    _assert(window.root.destroy_count == 0, "normal Stop destroyed root")


def _test_controls_unlock_only_after_worker_exit() -> None:
    worker = _FakeWorker(alive=True)
    controller = _FakeController(active_process=False)
    window = _fake_window(worker, controller)

    window._handle_download_worker_finished("completed", "")
    window.start_download()

    _assert(window.downloading, "download state cleared before worker exit")
    _assert(window.download_worker is worker, "worker reference cleared before exit")
    _assert(False not in window.controls_locked, "controls unlocked before worker exit")
    _assert(window.root.destroy_count == 0, "root destroyed during normal Stop completion")

    worker.alive = False
    window.file_start_number_var.set("53")
    window._poll_download_finish_completion()

    _assert(not window.downloading, "download state was not cleared after worker exit")
    _assert(window.download_worker is None, "worker reference was not cleared after exit")
    _assert(window.download_controller is None, "controller reference was not cleared after exit")
    _assert(window.controls_locked and window.controls_locked[-1] is False, "controls were not unlocked")
    _assert(window.file_start_number_var.get() == "53", "finish reset the advanced file number")
    _assert(window._active_download_run_id is None, "finished run still accepted numbering events")
    _assert(window.root.destroy_count == 0, "normal finish destroyed root")


def _test_repeated_close_does_not_duplicate_shutdown() -> None:
    controller = _FakeController(active_process=True)
    window = _fake_window(_FakeWorker(alive=True), controller)

    window._on_close()
    log_count = len(window.logs)
    scheduled_count = len(window.root.scheduled)
    poll_id = window._shutdown_poll_after_id

    window._on_close()

    _assert(window.confirm_calls == 1, "second close showed confirmation again")
    _assert(controller.cancel_requests == 1, "second close requested cancellation again")
    _assert(len(window.logs) == log_count, "second close duplicated log lines")
    _assert(len(window.root.scheduled) == scheduled_count, "second close scheduled another poll")
    _assert(window._shutdown_poll_after_id == poll_id, "shutdown poll id changed on second close")
    _assert(window.root.destroy_count == 0, "second close destroyed root early")


def _test_terminal_event_before_thread_exit() -> None:
    worker = _FakeWorker(alive=True)
    window = _fake_window(worker, _FakeController(active_process=False))

    window._handle_download_worker_finished("completed", "")

    _assert(window.downloading, "terminal event finalized UI while worker was alive")
    _assert(False not in window.controls_locked, "terminal event unlocked controls while worker was alive")
    _assert(window.download_worker is worker, "terminal event cleared live worker")
    _assert(window.root.destroy_count == 0, "terminal event destroyed root while worker was alive")

    worker.alive = False
    window._poll_download_finish_completion()

    _assert(not window.downloading, "UI did not finalize after worker exit")
    _assert(window.download_worker is None, "stale worker reference remained after finalization")


def _test_thread_exit_before_terminal_event() -> None:
    window = _fake_window(_FakeWorker(alive=False), _FakeController(active_process=False))

    window._handle_download_worker_finished("completed", "")

    _assert(not window.downloading, "UI did not finalize when worker had already exited")
    _assert(window.download_worker is None, "worker reference remained after terminal event")
    _assert(window.root.destroy_count == 0, "normal terminal event destroyed root")


def _test_error_terminal_event_during_shutdown() -> None:
    window = _fake_window(_FakeWorker(alive=False), _FakeController(active_process=False))
    window.shutdown_in_progress = True
    window.exit_after_download_stop = True

    window._handle_download_worker_finished("error", "friendly error")

    _assert("friendly error" in window.logs, "error terminal message was not logged")
    _assert(False not in window.controls_locked, "shutdown error terminal unlocked controls")
    _assert(window.root.destroy_count == 1, "root did not close after shutdown error terminal")


def _test_exit_wakes_systemic_pause() -> None:
    controller = DownloadController()
    window = _fake_window(None, controller)
    context = SystemicBlockContext(
        block_id="shutdown-systemic",
        failure_kind=YtdlpFailureKind.RATE_LIMIT,
        retry_allowed=False,
        reason="rate limited",
    )
    decisions = []
    dialog_calls = []
    controller.systemic_block_callback = lambda queued_context: window.events.put(
        ("systemic_download_block", queued_context)
    )

    def worker_target() -> None:
        decisions.append(controller.wait_for_systemic_decision(context))
        window.events.put(("download_worker_finished", "completed", ""))

    worker = threading.Thread(target=worker_target, daemon=True)
    window.download_worker = worker
    worker.start()
    _wait_for(lambda: not window.events.empty(), "systemic pause event was not queued")

    with _patched_dialog(lambda *_args, **_kwargs: dialog_calls.append("dialog") or BatchDecision.STOP_BATCH.value):
        window._exit_while_downloading()
        window._process_events()
        worker.join(timeout=5)
        _assert(not worker.is_alive(), "exit did not wake systemic decision wait")
        while not window.events.empty():
            window._handle_event(window.events.get_nowait())
        window._poll_shutdown_completion()

    _assert(decisions == [BatchDecision.STOP_BATCH], f"systemic decision was wrong: {decisions}")
    _assert(not dialog_calls, "pause dialog opened after shutdown started")
    _assert(window.root.destroy_count == 1, "root did not close after systemic worker exit")


def _test_slow_shutdown_warns_once_without_forced_destroy() -> None:
    controller = _FakeController(active_process=True)
    window = _fake_window(_FakeWorker(alive=True), controller)
    window.shutdown_in_progress = True
    window.shutdown_started_at = time.monotonic() - main_window.SHUTDOWN_SLOW_WARNING_SECONDS - 1

    window._poll_shutdown_completion()
    window._poll_shutdown_completion()

    warning_count = sum("m\u1ea5t nhi\u1ec1u th\u1eddi gian" in message for message in window.logs)
    _assert(window.root.destroy_count == 0, "slow shutdown forced root destruction")
    _assert(warning_count == 1, f"slow shutdown warning count was wrong: {warning_count}")
    _assert(controller.cancel_requests == 1, f"slow shutdown cancel reissue count was wrong: {controller.cancel_requests}")
    _assert(window._shutdown_poll_after_id is not None, "slow shutdown polling stopped")


def _test_destroy_root_once_and_periodic_callbacks_stop() -> None:
    window = _fake_window(_FakeWorker(alive=False), _FakeController(active_process=False))
    window.shutdown_in_progress = True
    window._download_terminal_received = True

    window._poll_shutdown_completion()
    window._poll_shutdown_completion()
    window._destroy_root_once()
    scheduled_count = len(window.root.scheduled)

    window._process_events()
    window._poll_progress_queue()
    window._poll_cookie_status()

    _assert(window.root.destroy_count == 1, f"root destroy count was wrong: {window.root.destroy_count}")
    _assert(len(window.root.scheduled) == scheduled_count, "periodic callback rescheduled after root destruction")


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
    window._active_download_run_id = 1
    window.file_start_number_var = _Var()
    window.progress_current_var = _Var()
    window.progress_detail_var = _Var()
    window.logs = []
    window.controls_locked = []
    window.progress_events = []
    window.confirm_calls = 0
    window._append_log = lambda message: window.logs.append(message)
    window._set_download_controls_locked = lambda locked: window.controls_locked.append(locked)
    window._update_stop_button_state = lambda: None
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


class _FakeWorker:
    def __init__(self, alive: bool) -> None:
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


class _FakeController:
    def __init__(self, active_process: bool) -> None:
        self.active_process = active_process
        self.cancel_requests = 0

    def request_cancel(self) -> None:
        self.cancel_requests += 1

    def has_active_process(self) -> bool:
        return self.active_process


class _Var:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value


class _patched_dialog:
    def __init__(self, replacement) -> None:
        self.replacement = replacement
        self.original = None

    def __enter__(self):
        self.original = main_window.show_app_dialog
        main_window.show_app_dialog = self.replacement
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        main_window.show_app_dialog = self.original


def _wait_for(condition, message: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError(message)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
