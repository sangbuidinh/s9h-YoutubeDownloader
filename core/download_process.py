"""Process lifecycle and cancellation primitives for download operations."""

import os
import subprocess
import threading
import time

from core.download_contracts import (
    BatchDecision,
    DownloadCancelled,
    SystemicBlockContext,
)


class DownloadController:
    def __init__(self, systemic_block_callback=None):
        self._cancel_requested = threading.Event()
        self._process_lock = threading.Lock()
        self._decision_condition = threading.Condition()
        self._active_block_id = ""
        self._systemic_decision: BatchDecision | None = None
        self.systemic_block_callback = systemic_block_callback
        self.current_process: subprocess.Popen | None = None
        self._active_processes: set[subprocess.Popen] = set()

    def request_cancel(self) -> None:
        self._cancel_requested.set()
        with self._decision_condition:
            self._systemic_decision = BatchDecision.STOP_BATCH
            self._decision_condition.notify_all()
        with self._process_lock:
            processes = list(self._active_processes)
            if self.current_process is not None and self.current_process not in processes:
                processes.append(self.current_process)
        for process in processes:
            threading.Thread(target=self._terminate_process, args=(process,), daemon=True).start()

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def set_current_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            self._active_processes.add(process)
            self.current_process = process
        if self.is_cancel_requested():
            self._terminate_process(process)

    def clear_current_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            self._active_processes.discard(process)
            if self.current_process is process:
                self.current_process = next(iter(self._active_processes), None)

    def has_active_process(self) -> bool:
        with self._process_lock:
            processes = list(self._active_processes)
        for process in processes:
            try:
                if process.poll() is None:
                    return True
            except Exception:
                continue
        return False

    def is_idle(self) -> bool:
        with self._decision_condition:
            waiting_for_decision = bool(
                self._active_block_id
                and self._systemic_decision is None
                and not self.is_cancel_requested()
            )
        return not self.has_active_process() and not waiting_for_decision

    def wait_for_systemic_decision(self, context: SystemicBlockContext) -> BatchDecision:
        callback = self.systemic_block_callback
        if callback is None:
            return BatchDecision.STOP_BATCH

        with self._decision_condition:
            if self.is_cancel_requested():
                return BatchDecision.STOP_BATCH
            self._active_block_id = context.block_id
            self._systemic_decision = None

        try:
            callback(context)
        except Exception:
            return BatchDecision.STOP_BATCH

        with self._decision_condition:
            while self._systemic_decision is None and not self.is_cancel_requested():
                self._decision_condition.wait(timeout=0.25)
            decision = self._systemic_decision or BatchDecision.STOP_BATCH
            if self._active_block_id == context.block_id:
                self._active_block_id = ""
                self._systemic_decision = None
            return decision

    def submit_systemic_decision(self, block_id: str, decision: BatchDecision | str) -> bool:
        try:
            normalized = decision if isinstance(decision, BatchDecision) else BatchDecision(str(decision))
        except ValueError:
            return False
        with self._decision_condition:
            if block_id != self._active_block_id:
                return False
            self._systemic_decision = normalized
            self._decision_condition.notify_all()
            return True

    def is_systemic_block_active(self, block_id: str) -> bool:
        with self._decision_condition:
            return bool(
                block_id
                and block_id == self._active_block_id
                and self._systemic_decision is None
                and not self.is_cancel_requested()
            )

    def _terminate_process(self, process: subprocess.Popen) -> None:
        _terminate_process_tree(process)


def _subprocess_creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        0,
    )


def _terminate_process_tree(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        if process.poll() is not None:
            return
    except Exception:
        return

    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                return
        except Exception:
            pass

    try:
        process.terminate()
    except Exception:
        pass

    if _wait_for_process_exit(process, 2.0) is not None:
        return

    try:
        if process.poll() is None:
            process.kill()
    except Exception:
        pass
    _wait_for_process_exit(process, 2.0)


def _wait_for_process_exit(process: subprocess.Popen, timeout: float) -> int | None:
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        try:
            return process.poll()
        except Exception:
            return None


def _cancel_requested(cancel_controller: DownloadController | None) -> bool:
    return bool(cancel_controller and cancel_controller.is_cancel_requested())


def _raise_if_cancelled(cancel_controller: DownloadController | None) -> None:
    if _cancel_requested(cancel_controller):
        raise DownloadCancelled("download cancelled/interrupted")


def _sleep_with_cancel(seconds: int | float, cancel_controller: DownloadController | None) -> None:
    end_time = time.monotonic() + max(0, seconds)
    while True:
        _raise_if_cancelled(cancel_controller)
        remaining = end_time - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.25, remaining))
