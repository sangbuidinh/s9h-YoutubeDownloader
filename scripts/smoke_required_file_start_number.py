import sys
import tkinter as tk
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui import main_window


def main() -> int:
    _test_required_file_start_number_ui()
    _test_no_file_start_number_persistence()
    print("required file start number UI smoke passed")
    return 0


def _test_required_file_start_number_ui() -> None:
    root = tk.Tk()
    root.withdraw()
    window = main_window.YouTubeDownloaderWindow(root)
    try:
        _assert(window.file_start_number_var.get() == "", "file start number was not blank by default")
        _assert(window._validate_file_start_number_input(""), "blank edit was not allowed")
        _assert(window._validate_file_start_number_input("51"), "digits were not allowed")
        _assert(not window._validate_file_start_number_input("-1"), "negative input was allowed by key filter")

        errors: list[tuple[str, str]] = []
        focused: list[bool] = []
        started_threads: list[object] = []
        captured_options: list[object] = []

        video = SimpleNamespace(
            video_id="video-1",
            title="Video One",
            sanitized_filename_base="Video One",
            duration="",
            published_at="",
            status="",
            display_order=1,
        )

        window.channel_info = SimpleNamespace(channel_id="channel", channel_name="Channel")
        window.videos = [video]
        window.selected_orders = {1}
        window.visible_orders = [1]
        window._selected_visible_videos = lambda: [video]
        window._videos_for_snapshot_ids = lambda _ids: [video]
        window._show_error_dialog = lambda message, title="Lỗi", detail=None: errors.append((title, message))
        window.file_start_number_entry.focus_set = lambda: focused.append(True)

        with TemporaryDirectory(prefix="file_start_ui_") as temp_dir:
            window.save_folder_var.set(temp_dir)
            old_validate_env = main_window.validate_download_environment
            old_save_prefs = main_window.save_cookie_preferences
            old_thread = main_window.threading.Thread

            class FakeThread:
                def __init__(self, *, target, args, daemon):
                    self.target = target
                    self.args = args
                    self.daemon = daemon
                    captured_options.append(args[1])

                def start(self):
                    started_threads.append(self)

                def is_alive(self):
                    return False

            try:
                main_window.validate_download_environment = lambda _options: None
                main_window.save_cookie_preferences = lambda *_args, **_kwargs: True
                main_window.threading.Thread = FakeThread

                window.file_start_number_var.set("")
                window.start_download()
                _assert(errors, "blank value did not show an error")
                _assert(errors[-1][0] == "Starting file number required", "blank value used wrong dialog title")
                _assert(focused, "blank value did not focus the start-number field")
                _assert(not started_threads, "blank value started a worker")

                for invalid in ("0", "-1", "1.5", "abc"):
                    errors.clear()
                    focused.clear()
                    window.file_start_number_var.set(invalid)
                    window.start_download()
                    _assert(errors, f"{invalid!r} did not show an error")
                    _assert(focused, f"{invalid!r} did not focus the field")
                    _assert(not started_threads, f"{invalid!r} started a worker")

                window._set_download_controls_locked(True)
                _assert(str(window.file_start_number_entry.cget("state")) == "disabled", "field did not lock")
                window._set_download_controls_locked(False)
                _assert(str(window.file_start_number_entry.cget("state")) == "normal", "field did not unlock")

                window.file_start_number_var.set("51")
                window.start_download()
                _assert(started_threads, "valid value did not create a worker")
                _assert(captured_options[-1].file_start_number == 51, "valid value was not passed to DownloadOptions")
            finally:
                main_window.validate_download_environment = old_validate_env
                main_window.save_cookie_preferences = old_save_prefs
                main_window.threading.Thread = old_thread
    finally:
        root.destroy()


def _test_no_file_start_number_persistence() -> None:
    settings_source = (REPO_ROOT / "core" / "app_settings.py").read_text(encoding="utf-8")
    _assert("file_start_number" not in settings_source, "file_start_number was added to app settings")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
