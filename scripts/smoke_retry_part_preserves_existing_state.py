import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_store
from core.download_modes import MODE_VIDEO_AUDIO_THUMB, MODE_VIDEO_THUMB, PART_AUDIO, PART_THUMB, PART_VIDEO


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"
SAVE_BASE_FOLDER = "D:/A"


def main() -> int:
    _configure_stdio()
    real_runtime_before = _snapshot_real_runtime_files()
    _assert_common_sqlite_updates_are_metadata_only()
    _test_sqlite_thumbnail_retry_preserves_existing_video()
    _test_sqlite_failed_retry_does_not_downgrade_video()
    _test_sqlite_audio_retry_preserves_video_and_thumb()
    _test_json_thumbnail_retry_preserves_existing_video()
    _assert(
        real_runtime_before == _snapshot_real_runtime_files(),
        "real runtime state files were mutated by temp-file smoke tests",
    )
    print("retry part preservation smoke tests passed")
    return 0


def _test_sqlite_thumbnail_retry_preserves_existing_video() -> None:
    with _temp_runtime() as paths:
        old_video_path = "D:/A/Channel/video/old_video.mp4"
        _seed_sqlite_part_state(
            paths["db_path"],
            "retry-thumb",
            {
                PART_VIDEO: (state_store.STATUS_DOWNLOADED, "old_video.mp4", old_video_path),
                PART_THUMB: (state_store.STATUS_ERROR, "old_thumb.jpg", "D:/A/Channel/thumb/old_thumb.jpg"),
            },
        )
        run_paths = _run_paths(paths["data_dir"], video_exists=False, thumb_exists=True, audio_exists=False)
        video = _video("retry-thumb")

        with _patched_sqlite_db(paths["db_path"]):
            before = db_store.get_video_entry(CHANNEL_ID, video.video_id)
            state_store._sqlite_update_video_part_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                SAVE_BASE_FOLDER,
                video,
                run_paths,
                PART_THUMB,
                state_store.STATUS_DOWNLOADED,
                MODE_VIDEO_THUMB,
            )
            state_store._sqlite_reconcile_downloaded_item_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                SAVE_BASE_FOLDER,
                video,
                run_paths,
                MODE_VIDEO_THUMB,
            )
            after = db_store.get_video_entry(CHANNEL_ID, video.video_id)

        _assert_no_downloaded_part_downgraded(before, after, (PART_VIDEO, PART_THUMB))
        _assert(after["video_status"] == state_store.STATUS_DOWNLOADED, "SQLite video status was downgraded")
        _assert(after["video_filename"] == "old_video.mp4", "SQLite video filename was overwritten")
        _assert(after["video_path"] == old_video_path, "SQLite video path was overwritten")
        _assert(after["thumb_status"] == state_store.STATUS_DOWNLOADED, "SQLite thumb status was not promoted")
        _assert(after["status"] == state_store.STATUS_DOWNLOADED, "SQLite aggregate status did not become downloaded")


def _test_sqlite_failed_retry_does_not_downgrade_video() -> None:
    with _temp_runtime() as paths:
        _seed_sqlite_part_state(
            paths["db_path"],
            "retry-thumb-failed",
            {
                PART_VIDEO: (state_store.STATUS_DOWNLOADED, "old_video.mp4", "D:/A/Channel/video/old_video.mp4"),
                PART_THUMB: (state_store.STATUS_ERROR, "old_thumb.jpg", "D:/A/Channel/thumb/old_thumb.jpg"),
            },
        )
        run_paths = _run_paths(paths["data_dir"], video_exists=False, thumb_exists=False, audio_exists=False)
        video = _video("retry-thumb-failed")

        with _patched_sqlite_db(paths["db_path"]):
            before = db_store.get_video_entry(CHANNEL_ID, video.video_id)
            _old_thumb_status = before["thumb_status"]
            _old_thumb_path = before["thumb_path"]
            _old_thumb_filename = before["thumb_filename"]
            state_store._sqlite_reconcile_downloaded_item_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                SAVE_BASE_FOLDER,
                video,
                run_paths,
                MODE_VIDEO_THUMB,
            )
            after = db_store.get_video_entry(CHANNEL_ID, video.video_id)

        _assert_no_downloaded_part_downgraded(before, after, (PART_VIDEO, PART_THUMB))
        _assert(after["video_status"] == state_store.STATUS_DOWNLOADED, "failed retry downgraded video")
        _assert(after["thumb_status"] == _old_thumb_status, "failed retry changed thumb status")
        _assert(after["thumb_path"] == _old_thumb_path, "failed retry changed thumb path")
        _assert(after["thumb_filename"] == _old_thumb_filename, "failed retry changed thumb filename")
        _assert(after["status"] == state_store.STATUS_MISSING_THUMB, "failed retry aggregate should miss thumb")
        _assert(after["status"] != state_store.STATUS_MISSING_VIDEO, "failed retry incorrectly marked missing video")


def _test_sqlite_audio_retry_preserves_video_and_thumb() -> None:
    with _temp_runtime() as paths:
        _seed_sqlite_part_state(
            paths["db_path"],
            "retry-audio",
            {
                PART_VIDEO: (state_store.STATUS_DOWNLOADED, "old_video.mp4", "D:/A/Channel/video/old_video.mp4"),
                PART_THUMB: (state_store.STATUS_DOWNLOADED, "old_thumb.jpg", "D:/A/Channel/thumb/old_thumb.jpg"),
                PART_AUDIO: (state_store.STATUS_ERROR, "old_audio.mp3", "D:/A/Channel/audio/old_audio.mp3"),
            },
            download_mode=MODE_VIDEO_AUDIO_THUMB,
        )
        run_paths = _run_paths(paths["data_dir"], video_exists=False, thumb_exists=False, audio_exists=True)
        video = _video("retry-audio")

        with _patched_sqlite_db(paths["db_path"]):
            before = db_store.get_video_entry(CHANNEL_ID, video.video_id)
            state_store._sqlite_reconcile_downloaded_item_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                SAVE_BASE_FOLDER,
                video,
                run_paths,
                MODE_VIDEO_AUDIO_THUMB,
            )
            after = db_store.get_video_entry(CHANNEL_ID, video.video_id)

        _assert_no_downloaded_part_downgraded(before, after, (PART_VIDEO, PART_THUMB, PART_AUDIO))
        _assert(after["video_status"] == state_store.STATUS_DOWNLOADED, "audio retry downgraded video")
        _assert(after["thumb_status"] == state_store.STATUS_DOWNLOADED, "audio retry downgraded thumb")
        _assert(after["audio_status"] == state_store.STATUS_DOWNLOADED, "audio retry did not promote audio")
        _assert(after["status"] == state_store.STATUS_DOWNLOADED, "audio retry aggregate did not become downloaded")


def _test_json_thumbnail_retry_preserves_existing_video() -> None:
    with _temp_runtime() as paths:
        old_video_path = "D:/A/Channel/video/old_video.mp4"
        _write_json_state(
            paths["json_path"],
            "json-retry-thumb",
            {
                PART_VIDEO: (state_store.STATUS_DOWNLOADED, "old_video.mp4", old_video_path),
                PART_THUMB: (state_store.STATUS_ERROR, "old_thumb.jpg", "D:/A/Channel/thumb/old_thumb.jpg"),
            },
        )
        run_paths = _run_paths(paths["data_dir"], video_exists=False, thumb_exists=True, audio_exists=False)
        video = _video("json-retry-thumb")

        with _patched_json_paths(paths):
            before = state_store._json_get_video_entry(CHANNEL_ID, video.video_id)
            state_store._json_reconcile_downloaded_item_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                SAVE_BASE_FOLDER,
                video,
                run_paths,
                MODE_VIDEO_THUMB,
            )
            after = state_store._json_get_video_entry(CHANNEL_ID, video.video_id)

        _assert_no_downloaded_part_downgraded(before, after, (PART_VIDEO, PART_THUMB))
        _assert(after["video_status"] == state_store.STATUS_DOWNLOADED, "JSON video status was downgraded")
        _assert(after["video_filename"] == "old_video.mp4", "JSON video filename was overwritten")
        _assert(after["video_path"] == old_video_path, "JSON video path was overwritten")
        _assert(after["thumb_status"] == state_store.STATUS_DOWNLOADED, "JSON thumb status was not promoted")
        _assert(after["status"] == state_store.STATUS_DOWNLOADED, "JSON aggregate status did not become downloaded")


def _seed_sqlite_part_state(
    db_path: Path,
    video_id: str,
    parts: dict[str, tuple[str, str, str]],
    download_mode: str = MODE_VIDEO_THUMB,
) -> None:
    for part, (status, filename, file_path) in parts.items():
        db_store.update_video_part_state(
            CHANNEL_ID,
            video_id,
            part,
            filename=filename,
            file_path=file_path,
            status=status,
            path=db_path,
            save_base_folder=SAVE_BASE_FOLDER,
            download_mode=download_mode,
            channel_name=CHANNEL_NAME,
            original_title=f"Video {video_id}",
            sanitized_filename_base=video_id,
        )


def _write_json_state(
    path: Path,
    video_id: str,
    parts: dict[str, tuple[str, str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "channel_id": CHANNEL_ID,
        "channel_name": CHANNEL_NAME,
        "save_base_folder": SAVE_BASE_FOLDER,
        "video_id": video_id,
        "original_title": f"Video {video_id}",
        "sanitized_filename_base": video_id,
        "status": state_store.STATUS_MISSING_THUMB,
    }
    for part, (status, filename, file_path) in parts.items():
        entry[f"{part}_status"] = status
        entry[f"{part}_filename"] = filename
        entry[f"{part}_path"] = file_path
    state = {
        "version": 1,
        "channels": {
            CHANNEL_ID: {
                "channel_id": CHANNEL_ID,
                "channel_name": CHANNEL_NAME,
                "save_base_folder": SAVE_BASE_FOLDER,
                "videos": {video_id: entry},
            }
        },
    }
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_paths(data_dir: Path, video_exists: bool, thumb_exists: bool, audio_exists: bool):
    paths = SimpleNamespace(
        video_path=data_dir / "current" / "video" / "current_video.mp4",
        thumb_path=data_dir / "current" / "thumb" / "current_thumb.jpg",
        audio_path=data_dir / "current" / "audio" / "current_audio.mp3",
    )
    for path, should_exist in (
        (paths.video_path, video_exists),
        (paths.thumb_path, thumb_exists),
        (paths.audio_path, audio_exists),
    ):
        if should_exist:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
    return paths


def _video(video_id: str):
    return SimpleNamespace(
        video_id=video_id,
        title=f"Video {video_id}",
        sanitized_filename_base=video_id,
        display_order=1,
    )


def _assert_common_sqlite_updates_are_metadata_only() -> None:
    keys = set(state_store._sqlite_common_video_updates(CHANNEL_NAME, _video("metadata"), _run_paths(Path(os.devnull).parent, False, False, False)))
    forbidden = {
        "video_filename",
        "thumb_filename",
        "audio_filename",
        "video_path",
        "thumb_path",
        "audio_path",
    }
    _assert(not keys & forbidden, f"_sqlite_common_video_updates emitted part fields: {sorted(keys & forbidden)}")


def _assert_no_downloaded_part_downgraded(before: dict | None, after: dict | None, parts: tuple[str, ...]) -> None:
    for part in parts:
        before_status = state_store.part_status_from_entry(before, part)
        after_status = state_store.part_status_from_entry(after, part)
        if before_status == state_store.STATUS_DOWNLOADED:
            _assert(
                after_status == state_store.STATUS_DOWNLOADED,
                f"{part} was downgraded from downloaded to {after_status}",
            )


@contextmanager
def _temp_runtime():
    with TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir) / "data"
        yield {
            "data_dir": data_dir,
            "json_path": data_dir / "download_state.json",
            "db_path": data_dir / "download_state.sqlite3",
        }


@contextmanager
def _patched_sqlite_db(db_path: Path):
    old_db_file = db_store.db_file
    try:
        db_store.db_file = lambda: db_path
        yield
    finally:
        db_store.db_file = old_db_file


@contextmanager
def _patched_json_paths(paths: dict):
    old_state_file = state_store.state_file
    old_data_dir = state_store.data_dir
    try:
        state_store.state_file = lambda: paths["json_path"]
        state_store.data_dir = lambda: paths["data_dir"]
        yield
    finally:
        state_store.state_file = old_state_file
        state_store.data_dir = old_data_dir


def _snapshot_real_runtime_files() -> dict[str, tuple[bool, int | None, int | None]]:
    paths = {
        "json": state_store.state_file(),
        "sqlite": db_store.db_file(),
        "wal": Path(f"{db_store.db_file()}-wal"),
        "shm": Path(f"{db_store.db_file()}-shm"),
    }
    snapshot = {}
    for label, path in paths.items():
        try:
            stat = path.stat()
        except FileNotFoundError:
            snapshot[label] = (False, None, None)
        else:
            snapshot[label] = (True, stat.st_size, stat.st_mtime_ns)
    return snapshot


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
