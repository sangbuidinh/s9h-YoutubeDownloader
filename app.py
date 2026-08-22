import sys
import tkinter as tk
from tkinter import messagebox

from core import downloader
from core.runtime_diagnostics import (
    install_downloader_diagnostics,
    install_ui_diagnostics,
    load_diagnostic_session,
)
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
    diagnostic_session = load_diagnostic_session()
    if diagnostic_session is not None:
        install_downloader_diagnostics(downloader, diagnostic_session)

    try:
        initialize_sqlite_state()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if diagnostic_session is not None:
            diagnostic_session.write("startup_exception", error_type=type(exc).__name__, message=detail)
            diagnostic_session.close()
        _show_startup_error(f"{SQLITE_OPEN_ERROR_MESSAGE}\n\nChi tiết: {detail}")
        raise SystemExit(1)

    _configure_cookie_media_strategy()

    from ui import main_window

    if diagnostic_session is not None:
        install_ui_diagnostics(main_window, diagnostic_session)

    try:
        main_window.main()
    finally:
        if diagnostic_session is not None:
            diagnostic_session.close()
