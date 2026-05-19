import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
LEGACY_RUNTIME_DIR = Path(r"D:\Youtube Downloader")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


def data_dir() -> Path:
    return app_root() / "data"


def state_file() -> Path:
    return data_dir() / "download_state.json"


def db_file() -> Path:
    return data_dir() / "download_state.sqlite3"


def runtime_file(filename: str) -> Path:
    primary = app_root() / filename
    if primary.exists() or is_frozen():
        return primary
    fallback = LEGACY_RUNTIME_DIR / filename
    if fallback.exists():
        return fallback
    return primary
