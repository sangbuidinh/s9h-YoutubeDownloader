import sys
import tkinter as tk
from tkinter import messagebox

from core import downloader
from core.state_store import SQLITE_OPEN_ERROR_MESSAGE, initialize_sqlite_state


def _show_startup_error(message: str) -> None:
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("SQLite Error", message)
        root.destroy()
    except Exception:
        print(f"[ERROR] {message}", file=sys.stderr)


def _configure_cookie_media_strategy() -> None:
    # The confirmed stable route is authenticated metadata followed by a
    # cookieless media transfer after the signed URLs are at least 10 seconds old.
    # One-video lookahead prepares that metadata while the current video downloads,
    # so the age requirement is normally satisfied without an idle pause.
    downloader.COOKIE_MEDIA_RETRY_TARGET_SECONDS = (10, 30)

    # Keep both learned targets sticky. Using 10 here would make a successful
    # 30-second fallback immediately probe 10 seconds again on the next video.
    downloader.COOKIE_MEDIA_SHORT_PROBE_SECONDS = 30
    downloader.COOKIE_MEDIA_PROBE_INTERVAL_VIDEOS = 2**31 - 1


if __name__ == "__main__":
    try:
        initialize_sqlite_state()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        _show_startup_error(f"{SQLITE_OPEN_ERROR_MESSAGE}\n\nChi tiết: {detail}")
        raise SystemExit(1)

    _configure_cookie_media_strategy()

    from ui.main_window import main

    main()
