import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_store
from core.download_modes import MODE_VIDEO_THUMB


def main() -> int:
    _configure_stdio()
    with TemporaryDirectory() as td:
        db_path = Path(td) / "download_state.sqlite3"
        db_store.init_db(db_path)
        _seed_sqlite_state(db_path)
        with _patched_db_file(db_path):
            _run_cases()
    print("manual status clear smoke tests passed")
    return 0


def _run_cases() -> None:
    _case_not_downloaded_returns_not_downloaded()
    _case_downloaded_returns_downloaded()
    _case_missing_thumb_returns_missing_thumb()


def _case_not_downloaded_returns_not_downloaded() -> None:
    video = _video("not-downloaded")
    state_store.update_manual_status(
        "channel",
        "Channel",
        "D:/Out",
        video,
        state_store.STATUS_MISSING_THUMB,
    )
    entry = state_store.get_video_entry("channel", "not-downloaded")
    _assert(
        state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_MISSING_THUMB,
        "manual missing thumb was not effective",
    )
    state_store.clear_manual_status("channel", "Channel", "D:/Out", video, download_mode=MODE_VIDEO_THUMB)
    entry = state_store.get_video_entry("channel", "not-downloaded")
    _assert(
        state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_NOT_DOWNLOADED,
        "clear manual did not return to not downloaded",
    )


def _case_downloaded_returns_downloaded() -> None:
    video = _video("downloaded")
    state_store.update_manual_status("channel", "Channel", "D:/Out", video, state_store.STATUS_NOT_DOWNLOADED)
    entry = state_store.get_video_entry("channel", "downloaded")
    _assert(
        state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_NOT_DOWNLOADED,
        "manual not downloaded was not effective",
    )
    state_store.clear_manual_status("channel", "Channel", "D:/Out", video, download_mode=MODE_VIDEO_THUMB)
    entry = state_store.get_video_entry("channel", "downloaded")
    _assert(
        state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_DOWNLOADED,
        "clear manual did not return to downloaded",
    )


def _case_missing_thumb_returns_missing_thumb() -> None:
    video = _video("missing-thumb")
    state_store.update_manual_status("channel", "Channel", "D:/Out", video, state_store.STATUS_DOWNLOADED)
    entry = state_store.get_video_entry("channel", "missing-thumb")
    _assert(
        state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_DOWNLOADED,
        "manual downloaded was not effective",
    )
    state_store.clear_manual_status("channel", "Channel", "D:/Out", video, download_mode=MODE_VIDEO_THUMB)
    entry = state_store.get_video_entry("channel", "missing-thumb")
    _assert(
        state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_MISSING_THUMB,
        "clear manual did not return to missing thumbnail",
    )


def _seed_sqlite_state(path: Path) -> None:
    db_store.update_video_state(
        "channel",
        "not-downloaded",
        {
            "channel_name": "Channel",
            "sanitized_filename_base": "not-downloaded",
            "status": state_store.STATUS_NOT_DOWNLOADED,
        },
        path=path,
        save_base_folder="D:/Out",
    )
    db_store.update_video_state(
        "channel",
        "downloaded",
        {
            "channel_name": "Channel",
            "sanitized_filename_base": "downloaded",
            "status": state_store.STATUS_DOWNLOADED,
        },
        path=path,
        save_base_folder="D:/Out",
    )
    db_store.update_video_state(
        "channel",
        "missing-thumb",
        {
            "channel_name": "Channel",
            "sanitized_filename_base": "missing-thumb",
            "status": state_store.STATUS_MISSING_THUMB,
            "video_status": state_store.STATUS_DOWNLOADED,
            "thumb_status": state_store.STATUS_NOT_DOWNLOADED,
        },
        path=path,
        save_base_folder="D:/Out",
    )


@contextmanager
def _patched_db_file(db_path: Path):
    old_state_db_file = state_store.db_file
    old_db_store_db_file = db_store.db_file
    try:
        state_store.db_file = lambda: db_path
        db_store.db_file = lambda: db_path
        yield
    finally:
        state_store.db_file = old_state_db_file
        db_store.db_file = old_db_store_db_file


def _video(video_id: str):
    return SimpleNamespace(
        video_id=video_id,
        title=video_id,
        sanitized_filename_base=video_id,
        display_order=1,
    )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
