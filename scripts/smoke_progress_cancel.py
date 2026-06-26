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


def main() -> int:
    _test_terminate_process_tree_stops_dummy_process()
    if os.name == "nt":
        _test_terminate_process_tree_kills_windows_child_process()
    _test_streamed_runner_cancels_quickly()
    _test_cancelled_batch_does_not_emit_batch_completed()
    print("progress cancel smoke tests passed")
    return 0


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
                DownloadOptions(str(Path(temp_dir)), "channel", "Channel", download_mode=MODE_VIDEO_THUMB),
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
