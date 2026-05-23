import sys
import tkinter as tk
from tkinter import messagebox

from core.state_store import SQLITE_OPEN_ERROR_MESSAGE, initialize_sqlite_state


def _show_startup_error(message: str) -> None:
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("SQLite Error", message)
        root.destroy()
    except Exception:
        print(f"[ERROR] {message}", file=sys.stderr)


if __name__ == "__main__":
    try:
        initialize_sqlite_state()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        _show_startup_error(f"{SQLITE_OPEN_ERROR_MESSAGE}\n\nChi tiết: {detail}")
        raise SystemExit(1)

    from ui.main_window import main

    main()
