import re
import sys
import tkinter as tk
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui import main_window


def main() -> int:
    _test_initial_size_expands_to_content()
    _test_initial_size_respects_display_bounds()
    _test_real_window_geometry_fits_requested_height()
    print("responsive window layout smoke passed")
    return 0


def _test_initial_size_expands_to_content() -> None:
    width, height = main_window._initial_window_size(
        requested_height=751,
        screen_width=1920,
        screen_height=1080,
        maximum_width=1924,
        maximum_height=1061,
    )
    _assert(width == 1440, f"preferred startup width changed: {width}")
    _assert(
        height == 751 + main_window.INITIAL_WINDOW_CONTENT_PADDING,
        f"startup height did not expand to content: {height}",
    )


def _test_initial_size_respects_display_bounds() -> None:
    width, height = main_window._initial_window_size(
        requested_height=1200,
        screen_width=1366,
        screen_height=768,
        maximum_width=1348,
        maximum_height=749,
    )
    _assert(width == 1348, f"startup width exceeded display bounds: {width}")
    _assert(height == 749, f"startup height exceeded display bounds: {height}")


def _test_real_window_geometry_fits_requested_height() -> None:
    root = tk.Tk()
    root.withdraw()
    window = main_window.YouTubeDownloaderWindow(root)
    try:
        try:
            root.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        root.deiconify()
        root.update()
        match = re.match(r"^(\d+)x(\d+)", root.geometry())
        _assert(match is not None, f"unexpected Tk geometry: {root.geometry()}")
        actual_width = int(match.group(1))
        actual_height = int(match.group(2))
        requested_height = root.winfo_reqheight()
        maximum_width, maximum_height = root.maxsize()
        expected_width, expected_height = main_window._initial_window_size(
            requested_height,
            root.winfo_screenwidth(),
            root.winfo_screenheight(),
            maximum_width,
            maximum_height,
        )
        _assert(actual_width == expected_width, "window did not apply responsive startup width")
        _assert(actual_height == expected_height, "window did not apply content-driven startup height")
        _assert(
            actual_height >= min(requested_height, expected_height),
            "startup geometry clipped the requested control height",
        )
        _assert(
            root.minsize()[1] >= min(requested_height, expected_height),
            "minimum height still permits clipping the download actions",
        )
        root_bottom = root.winfo_rooty() + root.winfo_height()
        download_actions_bottom = (
            window.download_button.winfo_rooty()
            + window.download_button.winfo_height()
        )
        _assert(
            download_actions_bottom <= root_bottom,
            "download actions are outside the startup client area",
        )
    finally:
        root.destroy()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
