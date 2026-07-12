import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.download_modes import MODE_VIDEO_THUMB
from core.downloader import DownloadController, DownloadOptions
from core.progress_status import ProgressEvent, format_progress_event_lines
from ui.main_window import YouTubeDownloaderWindow


def main() -> int:
    _test_windows_taskkill_success_skips_fallback()
    _test_windows_taskkill_failure_uses_terminate()
    _test_windows_terminate_timeout_uses_kill()
    _test_windows_taskkill_timeout_uses_fallback()
    _test_windows_taskkill_oserror_uses_fallback()
    _test_process_wait_errors_do_not_escape_cleanup()
    _test_terminate_process_tree_skips_exited_process()
    _test_terminate_process_tree_stops_dummy_process()
    if os.name == "nt":
        _test_terminate_process_tree_kills_windows_child_process()
    _test_streamed_runner_cancels_quickly()
    _test_aria2_progress_cancel_stops_events_and_logs_timing()
    _test_cancelled_batch_does_not_emit_batch_completed()
    print("progress cancel smoke tests passed")
    return 0


def _test_windows_taskkill_success_skips_fallback() -> None:
    process = _FakeProcess()
    calls = _run_fake_windows_termination(process, taskkill_result=SimpleNamespace(returncode=0))
    _assert(calls == 1, "successful taskkill was not called exactly once")
    _assert(process.terminate_calls == 0 and process.kill_calls == 0, "successful taskkill used fallback")


def _test_windows_taskkill_failure_uses_terminate() -> None:
    process = _FakeProcess(wait_results=[0])
    _run_fake_windows_termination(process, taskkill_result=SimpleNamespace(returncode=1))
    _assert(process.terminate_calls == 1, "failed taskkill did not call terminate")
    _assert(process.kill_calls == 0, "successful terminate unexpectedly called kill")


def _test_windows_terminate_timeout_uses_kill() -> None:
    process = _FakeProcess(wait_results=[subprocess.TimeoutExpired("fake", 2), 0])
    _run_fake_windows_termination(process, taskkill_result=SimpleNamespace(returncode=1))
    _assert(process.terminate_calls == 1, "terminate was not attempted")
    _assert(process.kill_calls == 1, "ineffective terminate did not call kill")


def _test_windows_taskkill_timeout_uses_fallback() -> None:
    process = _FakeProcess(wait_results=[0])
    _run_fake_windows_termination(process, taskkill_error=subprocess.TimeoutExpired("taskkill", 3))
    _assert(process.terminate_calls == 1, "taskkill timeout did not use terminate fallback")


def _test_windows_taskkill_oserror_uses_fallback() -> None:
    process = _FakeProcess(wait_results=[0])
    _run_fake_windows_termination(process, taskkill_error=OSError("taskkill unavailable"))
    _assert(process.terminate_calls == 1, "taskkill OSError did not use terminate fallback")


def _test_process_wait_errors_do_not_escape_cleanup() -> None:
    process = _WaitAndPollErrorProcess()
    _run_fake_windows_termination(process, taskkill_result=SimpleNamespace(returncode=1))
    _assert(process.terminate_calls == 1, "wait-error cleanup did not attempt terminate")


def _test_terminate_process_tree_skips_exited_process() -> None:
    process = _FakeProcess(initial_returncode=0)
    calls = _run_fake_windows_termination(process, taskkill_result=SimpleNamespace(returncode=0))
    _assert(calls == 0, "already-exited process invoked taskkill")
    _assert(process.terminate_calls == 0 and process.kill_calls == 0, "already-exited process used fallback")


def _run_fake_windows_termination(process, *, taskkill_result=None, taskkill_error=None) -> int:
    original_os = downloader.os
    original_run = downloader.subprocess.run
    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if taskkill_error is not None:
            raise taskkill_error
        return taskkill_result

    try:
        downloader.os = SimpleNamespace(name="nt")
        downloader.subprocess.run = fake_run
        downloader._terminate_process_tree(process)
    finally:
        downloader.os = original_os
        downloader.subprocess.run = original_run
    return calls


class _FakeProcess:
    def __init__(self, *, initial_returncode=None, wait_results=None) -> None:
        self.pid = 12345
        self.returncode = initial_returncode
        self.wait_results = list(wait_results or [])
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1

    def wait(self, timeout=None):
        if self.wait_results:
            result = self.wait_results.pop(0)
            if isinstance(result, BaseException):
                raise result
            self.returncode = result
            return result
        return self.returncode


class _WaitAndPollErrorProcess(_FakeProcess):
    def __init__(self) -> None:
        super().__init__()
        self.poll_calls = 0

    def poll(self):
        self.poll_calls += 1
        if self.poll_calls == 1:
            return None
        raise OSError("poll failed")

    def wait(self, timeout=None):
        raise OSError("wait failed")


def _test_terminate_process_tree_stops_dummy_process() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    start = time.monotonic()
    downloader._terminate_process_tree(process)
    elapsed = time.monotonic() - start

    _assert(elapsed < 5, f"process-tree termination was too slow: {elapsed:.2f}s")
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        raise AssertionError("dummy process was not terminated")


def _test_terminate_process_tree_kills_windows_child_process() -> None:
    parent_code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(30)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    child_pid = None
    try:
        assert parent.stdout is not None
        child_pid_text = parent.stdout.readline().strip()
        _assert(child_pid_text.isdigit(), f"child pid was not reported: {child_pid_text!r}")
        child_pid = int(child_pid_text)

        start = time.monotonic()
        downloader._terminate_process_tree(parent)
        elapsed = time.monotonic() - start
        _assert(elapsed < 5, f"Windows process-tree termination was too slow: {elapsed:.2f}s")
        try:
            parent.wait(timeout=1)
        except subprocess.TimeoutExpired:
            raise AssertionError("parent process was not terminated")

        time.sleep(0.5)
        _assert(not _windows_process_exists(child_pid), "child process was left running")
    finally:
        downloader._terminate_process_tree(parent)
        if child_pid is not None and _windows_process_exists(child_pid):
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
            )


def _test_streamed_runner_cancels_quickly() -> None:
    controller = DownloadController()
    result = []
    command = [
        sys.executable,
        "-c",
        (
            "import sys, time\n"
            "print('[download]  1.0% of 10.00MiB at 1.00MiB/s ETA 00:00:09', flush=True)\n"
            "time.sleep(30)\n"
        ),
    ]

    def run() -> None:
        try:
            downloader._run_ytdlp(command, controller)
        except downloader.DownloadCancelled:
            result.append("cancelled")
        except BaseException as exc:
            result.append(f"{type(exc).__name__}: {exc}")
        else:
            result.append("completed")

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    _wait_for_current_process(controller)
    _assert(controller.has_active_process(), "controller did not report active subprocess")

    start = time.monotonic()
    controller.request_cancel()
    worker.join(timeout=5)
    elapsed = time.monotonic() - start

    _assert(not worker.is_alive(), "streamed runner did not stop after cancellation")
    _assert(elapsed < 5, f"streamed runner cancellation was too slow: {elapsed:.2f}s")
    _assert(result == ["cancelled"], f"streamed runner result was wrong: {result}")
    _assert(not controller.has_active_process(), "controller still reported active subprocess after cleanup")
    _assert(controller.current_process is None, "current process was not cleared")


def _test_aria2_progress_cancel_stops_events_and_logs_timing() -> None:
    controller = DownloadController()
    events: list[ProgressEvent] = []
    logs: list[str] = []
    result: list[str] = []
    video = _video()
    code = (
        "import time\n"
        "print('[#abc 3MiB/100MiB(3%) CN:4 DL:8MiB]', flush=True)\n"
        "time.sleep(30)\n"
        "print('[#abc 90MiB/100MiB(90%) CN:4 DL:8MiB]', flush=True)\n"
    )

    with TemporaryDirectory(prefix="aria2_cancel_") as temp_dir:
        root = Path(temp_dir)
        final_path = root / "final.mp4"
        options = DownloadOptions(str(root), "channel", "Channel")
        old_builder = downloader._build_video_ytdlp_command
        downloader._build_video_ytdlp_command = lambda *_args, **_kwargs: [
            sys.executable,
            "-u",
            "-c",
            code,
            "--downloader",
            "aria2c",
        ]

        def run() -> None:
            previous = downloader._set_progress_context(events.append, video, 1, 1, "Video")
            try:
                downloader._download_video(
                    video.video_id,
                    video.sanitized_filename_base,
                    root,
                    final_path,
                    options,
                    logs.append,
                    controller,
                )
            except downloader.DownloadCancelled:
                result.append("cancelled")
            except BaseException as exc:
                result.append(f"{type(exc).__name__}: {exc}")
            else:
                result.append("completed")
            finally:
                downloader._restore_progress_context(previous)

        try:
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            _wait_for_aria2_progress(events)
            _assert(controller.has_active_process(), "aria2 fake process was not active")
            controller.request_cancel()
            worker.join(timeout=5)
            _assert(not worker.is_alive(), "aria2 fake process did not stop after cancellation")
            _assert(result == ["cancelled"], f"aria2 cancellation result was wrong: {result}")
            event_count = len(events)
            time.sleep(0.1)
            _assert(len(events) == event_count, "progress arrived after cancellation completed")
        finally:
            downloader._build_video_ytdlp_command = old_builder

    _assert(any(event.source == "aria2c" and event.percent == "3.0%" for event in events), "aria2 progress was not accepted")
    perf_lines = [line for line in logs if line.startswith("[PERF]")]
    _assert(len(perf_lines) == 1, f"cancellation emitted {len(perf_lines)} PERF lines")
    _assert("engine=aria2c" in perf_lines[0] and "result=cancelled" in perf_lines[0], "cancel PERF line was wrong")
    _assert("attempts=1" in perf_lines[0] and "retry_wait=0.00s" in perf_lines[0], "cancel retried unexpectedly")
    _assert(sum(1 for line in logs if line.startswith("[YT-DLP START]")) == 1, "retry started after cancellation")

    window = YouTubeDownloaderWindow.__new__(YouTubeDownloaderWindow)
    window._reset_progress_sticky(reset_order=True)
    for event in events:
        window._merge_progress_event_for_display(event)
    stopped = window._merge_progress_event_for_display(ProgressEvent(kind="stop_requested"))
    _current_line, detail_line = format_progress_event_lines(stopped)
    _assert("3.0%" not in detail_line, "cancelled UI retained stale aria2 percentage")


def _test_cancelled_batch_does_not_emit_batch_completed() -> None:
    controller = DownloadController()
    controller.request_cancel()
    events = []

    with TemporaryDirectory() as temp_dir:
        old_validate = downloader.validate_download_environment
        old_summary = downloader._log_runtime_tool_summary
        try:
            downloader.validate_download_environment = lambda _options: None
            downloader._log_runtime_tool_summary = lambda _log: None
            downloader.download_items(
                [_video()],
                DownloadOptions(
                    str(Path(temp_dir)),
                    "channel",
                    "Channel",
                    download_mode=MODE_VIDEO_THUMB,
                    file_start_number=1,
                ),
                lambda _message: None,
                lambda _video: None,
                cancel_controller=controller,
                progress_callback=events.append,
            )
        finally:
            downloader.validate_download_environment = old_validate
            downloader._log_runtime_tool_summary = old_summary

    kinds = [event.kind for event in events]
    _assert("stop_requested" in kinds, "cancelled batch did not emit stop_requested")
    _assert("batch_complete" not in kinds, "cancelled batch emitted batch_complete")


def _wait_for_current_process(controller: DownloadController) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if controller.has_active_process():
            return
        time.sleep(0.05)
    raise AssertionError("runner did not register current process")


def _wait_for_aria2_progress(events: list[ProgressEvent]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(event.source == "aria2c" and event.percent for event in events):
            return
        time.sleep(0.05)
    raise AssertionError("fake aria2 progress was not emitted")


def _windows_process_exists(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    return str(pid) in (result.stdout or "")


def _video():
    return SimpleNamespace(
        video_id="cancel-video",
        title="Cancel Video",
        sanitized_filename_base="cancel-video",
        display_order=1,
        thumbnail_url="",
        status="Chua tai",
    )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
