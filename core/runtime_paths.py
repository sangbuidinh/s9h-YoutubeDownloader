import os
import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
LEGACY_RUNTIME_DIR = Path(r"D:\Youtube Downloader")
RUNTIME_BIN_FILENAMES = {"yt-dlp.exe", "ffmpeg.exe", "deno.exe", "aria2c.exe"}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _legacy_runtime_enabled() -> bool:
    # Legacy source-tree compatibility is available only through explicit opt-in.
    return os.environ.get("S9H_ALLOW_LEGACY_RUNTIME", "").strip() == "1"


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


def data_dir() -> Path:
    return app_root() / "data"


def bin_dir() -> Path:
    return data_dir() / "bin"


def db_file() -> Path:
    return data_dir() / "download_state.sqlite3"


def runtime_file(filename: str) -> Path:
    if filename.casefold() in RUNTIME_BIN_FILENAMES:
        primary = bin_dir() / filename
        candidates = (primary, app_root() / filename)
    elif filename == "api key.txt":
        primary = data_dir() / filename
        candidates = (primary, app_root() / filename)
    else:
        primary = app_root() / filename
        candidates = (primary,)

    if not is_frozen() and _legacy_runtime_enabled():
        candidates = (*candidates, LEGACY_RUNTIME_DIR / filename)

    for path in candidates:
        if path.exists():
            return path
    return primary
