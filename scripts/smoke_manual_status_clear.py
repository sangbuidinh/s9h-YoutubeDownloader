import json
import os
import sys
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
    _run_json_cases()
    _run_sqlite_cases()
    print("manual status clear smoke tests passed")
    return 0


def _run_json_cases() -> None:
    old_state_file = state_store.state_file
    old_data_dir = state_store.data_dir
    previous_backend = os.environ.pop("YTDL_STATE_BACKEND", None)
    try:
        with TemporaryDirectory() as td:
            temp_dir = Path(td)
            state_path = temp_dir / "download_state.json"
            state_store.state_file = lambda: state_path
            state_store.data_dir = lambda: temp_dir
            os.environ["YTDL_STATE_BACKEND"] = "json"
            _write_json_state(state_path, _base_state())
            _run_cases("json")
    finally:
        state_store.state_file = old_state_file
        state_store.data_dir = old_data_dir
        if previous_backend is not None:
            os.environ["YTDL_STATE_BACKEND"] = previous_backend


def _run_sqlite_cases() -> None:
    old_state_db_file = state_store.db_file
    old_db_store_db_file = db_store.db_file
    previous_backend = os.environ.get("YTDL_STATE_BACKEND")
    try:
        with TemporaryDirectory() as td:
            db_path = Path(td) / "download_state.sqlite3"
            db_store.init_db(db_path)
            _seed_sqlite_state(db_path)
            state_store.db_file = lambda: db_path
            db_store.db_file = lambda: db_path
            os.environ["YTDL_STATE_BACKEND"] = "sqlite"
            _run_cases("sqlite")
    finally:
        state_store.db_file = old_state_db_file
        db_store.db_file = old_db_store_db_file
        if previous_backend is None:
            os.environ.pop("YTDL_STATE_BACKEND", None)
        else:
            os.environ["YTDL_STATE_BACKEND"] = previous_backend


def _run_cases(label: str) -> None:
    _case_not_downloaded_returns_not_downloaded(label)
    _case_downloaded_returns_downloaded(label)
    _case_missing_thumb_returns_missing_thumb(label)


def _case_not_downloaded_returns_not_downloaded(label: str) -> None:
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
        f"{label}: manual missing thumb was not effective",
    )
    state_store.clear_manual_status("channel", "Channel", "D:/Out", video, download_mode=MODE_VIDEO_THUMB)
    entry = state_store.get_video_entry("channel", "not-downloaded")
    _assert(
        state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_NOT_DOWNLOADED,
        f"{label}: clear manual did not return to not downloaded",
    )


def _case_downloaded_returns_downloaded(label: str) -> None:
    video = _video("downloaded")
    state_store.update_manual_status("channel", "Channel", "D:/Out", video, state_store.STATUS_NOT_DOWNLOADED)
    entry = state_store.get_video_entry("channel", "downloaded")
    _assert(
        state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_NOT_DOWNLOADED,
        f"{label}: manual not downloaded was not effective",
    )
    state_store.clear_manual_status("channel", "Channel", "D:/Out", video, download_mode=MODE_VIDEO_THUMB)
    entry = state_store.get_video_entry("channel", "downloaded")
    _assert(
        state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_DOWNLOADED,
        f"{label}: clear manual did not return to downloaded",
    )


def _case_missing_thumb_returns_missing_thumb(label: str) -> None:
    video = _video("missing-thumb")
    state_store.update_manual_status("channel", "Channel", "D:/Out", video, state_store.STATUS_DOWNLOADED)
    entry = state_store.get_video_entry("channel", "missing-thumb")
    _assert(
        state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_DOWNLOADED,
        f"{label}: manual downloaded was not effective",
    )
    state_store.clear_manual_status("channel", "Channel", "D:/Out", video, download_mode=MODE_VIDEO_THUMB)
    entry = state_store.get_video_entry("channel", "missing-thumb")
    _assert(
        state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_MISSING_THUMB,
        f"{label}: clear manual did not return to missing thumbnail",
    )


def _base_state() -> dict:
    return {
        "version": 1,
        "channels": {
            "channel": {
                "channel_id": "channel",
                "channel_name": "Channel",
                "save_base_folder": "D:/Out",
                "videos": {
                    "not-downloaded": {
                        "channel_id": "channel",
                        "channel_name": "Channel",
                        "save_base_folder": "D:/Out",
                        "video_id": "not-downloaded",
                        "sanitized_filename_base": "not-downloaded",
                        "status": state_store.STATUS_NOT_DOWNLOADED,
                    },
                    "downloaded": {
                        "channel_id": "channel",
                        "channel_name": "Channel",
                        "save_base_folder": "D:/Out",
                        "video_id": "downloaded",
                        "sanitized_filename_base": "downloaded",
                        "status": state_store.STATUS_DOWNLOADED,
                    },
                    "missing-thumb": {
                        "channel_id": "channel",
                        "channel_name": "Channel",
                        "save_base_folder": "D:/Out",
                        "video_id": "missing-thumb",
                        "sanitized_filename_base": "missing-thumb",
                        "status": state_store.STATUS_MISSING_THUMB,
                        "video_status": state_store.STATUS_DOWNLOADED,
                        "thumb_status": state_store.STATUS_NOT_DOWNLOADED,
                    },
                },
            }
        },
    }


def _write_json_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
