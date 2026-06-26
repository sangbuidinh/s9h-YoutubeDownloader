import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_store
from core.download_modes import MODE_VIDEO_THUMB, PART_THUMB, PART_VIDEO


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"
VIDEO_ID = "state-video"


def main() -> int:
    _configure_stdio()
    _test_all_read_apis_agree()
    _test_writes_never_create_folder_duplicates()
    _test_canonical_item_id_does_not_change()
    _test_manual_status_on_single_item()
    _test_current_attempt_positive_reconciliation()
    print("video state consistency smoke tests passed")
    return 0


def _test_all_read_apis_agree() -> None:
    with _temp_runtime() as paths:
        with _patched_db(paths["db_path"]):
            video = _video(VIDEO_ID)
            state_store.update_video_part_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/A",
                video,
                _paths_for("D:/A", "state-video"),
                PART_VIDEO,
                state_store.STATUS_DOWNLOADED,
                MODE_VIDEO_THUMB,
            )
            state_store.update_video_part_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/A",
                video,
                _paths_for("D:/A", "state-video"),
                PART_THUMB,
                state_store.STATUS_ERROR,
                MODE_VIDEO_THUMB,
            )
            state_store.update_manual_status(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/A",
                video,
                state_store.STATUS_NOT_DOWNLOADED,
            )

            loaded = state_store.load_state()
            channel_entries = state_store.get_channel_video_entries(CHANNEL_ID)
            video_entry = state_store.get_video_entry(CHANNEL_ID, VIDEO_ID)

        videos = loaded["channels"][CHANNEL_ID]["videos"]
        _assert(list(videos) == [VIDEO_ID], "load_state did not return exactly one video ID")
        _assert(not any("::" in key for key in videos), "load_state created a composite folder key")
        _assert(list(channel_entries) == [VIDEO_ID], "channel read did not return exactly one video ID")
        _assert(_logical_subset(videos[VIDEO_ID]) == _logical_subset(channel_entries[VIDEO_ID]), "load_state and channel read disagree")
        _assert(_logical_subset(video_entry) == _logical_subset(channel_entries[VIDEO_ID]), "video read and channel read disagree")


def _test_writes_never_create_folder_duplicates() -> None:
    with _temp_runtime() as paths:
        db_path = paths["db_path"]
        with _patched_db(db_path):
            video = _video("folder-writes")
            state_store.update_video_part_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/A",
                video,
                _paths_for("D:/A", "folder-writes-a"),
                PART_VIDEO,
                state_store.STATUS_ERROR,
                MODE_VIDEO_THUMB,
            )
            state_store.update_manual_status(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/A",
                video,
                state_store.STATUS_NOT_DOWNLOADED,
            )
            state_store.update_video_part_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/B",
                video,
                _paths_for("D:/B", "folder-writes-b"),
                PART_VIDEO,
                state_store.STATUS_DOWNLOADED,
                MODE_VIDEO_THUMB,
            )
            state_store.update_video_part_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/C",
                video,
                _paths_for("D:/C", "folder-writes-c"),
                PART_THUMB,
                state_store.STATUS_DOWNLOADED,
                MODE_VIDEO_THUMB,
            )
            entry = state_store.get_video_entry(CHANNEL_ID, "folder-writes")

        _assert(_item_count(db_path, "folder-writes") == 1, "writes created a second folder row")
        _assert(_path_text(entry["video_path"]) == "D:/B/Channel/video/folder-writes-b.mp4", "video part path did not reflect folder B write")
        _assert(_path_text(entry["thumb_path"]) == "D:/C/Channel/thumb/folder-writes-c.jpg", "thumb part path did not reflect folder C write")
        _assert(state_store.get_effective_status(entry) == state_store.STATUS_DOWNLOADED, "merged write state is not downloaded")


def _test_canonical_item_id_does_not_change() -> None:
    with _temp_runtime() as paths:
        db_path = paths["db_path"]
        with _patched_db(db_path):
            video = _video("stable-id")
            state_store.update_video_part_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/A",
                video,
                _paths_for("D:/A", "stable-id"),
                PART_VIDEO,
                state_store.STATUS_DOWNLOADED,
                MODE_VIDEO_THUMB,
            )
            item_id = _single_item_id(db_path, "stable-id")

            state_store.update_manual_status(CHANNEL_ID, CHANNEL_NAME, "D:/A", video, state_store.STATUS_ERROR)
            _assert(_single_item_id(db_path, "stable-id") == item_id, "manual override changed item ID")
            state_store.clear_manual_status(CHANNEL_ID, CHANNEL_NAME, "D:/A", video)
            _assert(_single_item_id(db_path, "stable-id") == item_id, "manual clear changed item ID")
            state_store.update_video_part_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/A",
                video,
                _paths_for("D:/A", "stable-id"),
                PART_THUMB,
                state_store.STATUS_ERROR,
                MODE_VIDEO_THUMB,
            )
            _assert(_single_item_id(db_path, "stable-id") == item_id, "part error changed item ID")
            state_store.update_video_part_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/B",
                video,
                _paths_for("D:/B", "stable-id"),
                PART_THUMB,
                state_store.STATUS_DOWNLOADED,
                MODE_VIDEO_THUMB,
            )
            _assert(_single_item_id(db_path, "stable-id") == item_id, "part success changed item ID")
            db_store.reconcile_downloaded_item_state(
                CHANNEL_ID,
                "stable-id",
                MODE_VIDEO_THUMB,
                path=db_path,
                save_base_folder="D:/B",
            )
            _assert(_single_item_id(db_path, "stable-id") == item_id, "aggregate reconciliation changed item ID")


def _test_manual_status_on_single_item() -> None:
    with _temp_runtime() as paths:
        db_path = paths["db_path"]
        with _patched_db(db_path):
            video = _video("manual")
            state_store.update_video_part_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/A",
                video,
                _paths_for("D:/A", "manual"),
                PART_VIDEO,
                state_store.STATUS_DOWNLOADED,
                MODE_VIDEO_THUMB,
            )
            state_store.update_manual_status(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/A",
                video,
                state_store.STATUS_NOT_DOWNLOADED,
            )
            _assert(_item_count(db_path, "manual") == 1, "manual update created duplicate items")
            entry = state_store.get_video_entry(CHANNEL_ID, "manual")
            _assert(entry["manual_override"] is True, "manual override not set")

            state_store.update_video_part_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/A",
                video,
                _paths_for("D:/A", "manual"),
                PART_THUMB,
                state_store.STATUS_DOWNLOADED,
                MODE_VIDEO_THUMB,
            )
            entry = state_store.get_video_entry(CHANNEL_ID, "manual")
            _assert("manual_override" not in entry, "successful part did not clear manual override")
            _assert(state_store.get_effective_status(entry) == state_store.STATUS_DOWNLOADED, "successful part did not recompute status")

            state_store.update_manual_status(CHANNEL_ID, CHANNEL_NAME, "D:/A", video, state_store.STATUS_ERROR)
            state_store.clear_manual_status(CHANNEL_ID, CHANNEL_NAME, "D:/A", video)
            entry = state_store.get_video_entry(CHANNEL_ID, "manual")
            _assert("manual_override" not in entry, "clear manual left override active")
            _assert(state_store.get_effective_status(entry) == state_store.STATUS_DOWNLOADED, "clear manual did not recompute status")

        _assert(not hasattr(db_store, "_merge_duplicate_video_entries"), "runtime duplicate merge helper still exists")


def _test_current_attempt_positive_reconciliation() -> None:
    with _temp_runtime() as paths:
        db_path = paths["db_path"]
        data_dir = paths["data_dir"]
        with _patched_db(db_path):
            success_video = _video("reconcile-success")
            old_missing_video_path = data_dir / "old" / "video.mp4"
            db_store.update_video_part_state(
                CHANNEL_ID,
                success_video.video_id,
                PART_VIDEO,
                filename="old.mp4",
                file_path=old_missing_video_path,
                status=state_store.STATUS_DOWNLOADED,
                path=db_path,
                save_base_folder="D:/A",
                channel_name=CHANNEL_NAME,
                sanitized_filename_base=success_video.sanitized_filename_base,
            )
            db_store.update_video_part_state(
                CHANNEL_ID,
                success_video.video_id,
                PART_THUMB,
                filename="old.jpg",
                file_path=data_dir / "old" / "thumb.jpg",
                status=state_store.STATUS_ERROR,
                path=db_path,
                save_base_folder="D:/A",
                channel_name=CHANNEL_NAME,
                sanitized_filename_base=success_video.sanitized_filename_base,
            )
            run_paths = _paths_for(str(data_dir / "run"), "success")
            run_paths.thumb_path.parent.mkdir(parents=True, exist_ok=True)
            run_paths.thumb_path.write_bytes(b"jpg")
            before = db_store.get_video_entry(CHANNEL_ID, success_video.video_id, path=db_path)
            state_store._sqlite_reconcile_downloaded_item_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/B",
                success_video,
                run_paths,
                MODE_VIDEO_THUMB,
                run_parts=(PART_THUMB,),
            )
            after = db_store.get_video_entry(CHANNEL_ID, success_video.video_id, path=db_path)
            _assert(before["video_status"] == state_store.STATUS_DOWNLOADED, "setup did not have unowned downloaded video")
            _assert(after["video_status"] == state_store.STATUS_DOWNLOADED, "unowned downloaded video was downgraded")
            _assert(after["video_path"] == str(old_missing_video_path), "unowned missing old path was changed")
            _assert(after["thumb_status"] == state_store.STATUS_DOWNLOADED, "run-owned existing thumb was not promoted")

            failed_video = _video("reconcile-failed")
            db_store.update_video_part_state(
                CHANNEL_ID,
                failed_video.video_id,
                PART_VIDEO,
                filename="old.mp4",
                file_path=data_dir / "old" / "failed-video.mp4",
                status=state_store.STATUS_DOWNLOADED,
                path=db_path,
                save_base_folder="D:/A",
                channel_name=CHANNEL_NAME,
                sanitized_filename_base=failed_video.sanitized_filename_base,
            )
            db_store.update_video_part_state(
                CHANNEL_ID,
                failed_video.video_id,
                PART_THUMB,
                filename="old.jpg",
                file_path=data_dir / "old" / "failed-thumb.jpg",
                status=state_store.STATUS_ERROR,
                path=db_path,
                save_base_folder="D:/A",
                channel_name=CHANNEL_NAME,
                sanitized_filename_base=failed_video.sanitized_filename_base,
            )
            missing_run_paths = _paths_for(str(data_dir / "missing-run"), "failed")
            before_failed = db_store.get_video_entry(CHANNEL_ID, failed_video.video_id, path=db_path)
            state_store._sqlite_reconcile_downloaded_item_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                "D:/B",
                failed_video,
                missing_run_paths,
                MODE_VIDEO_THUMB,
                run_parts=(PART_THUMB,),
            )
            after_failed = db_store.get_video_entry(CHANNEL_ID, failed_video.video_id, path=db_path)
            _assert(after_failed["video_status"] == state_store.STATUS_DOWNLOADED, "failed run downgraded unowned video")
            _assert(after_failed["thumb_status"] == before_failed["thumb_status"], "failed run-owned part changed without output")
            _assert(_item_count(db_path, success_video.video_id) == 1, "reconcile success created duplicate row")
            _assert(_item_count(db_path, failed_video.video_id) == 1, "reconcile failure created duplicate row")


def _logical_subset(entry: dict) -> dict:
    keys = (
        "video_id",
        "video_status",
        "thumb_status",
        "manual_status",
        "manual_override",
        "video_path",
        "thumb_path",
        "video_filename",
        "thumb_filename",
        "status",
    )
    return {key: entry.get(key) for key in keys if key in entry}


def _paths_for(base_folder: str, stem: str):
    channel_dir = Path(base_folder) / CHANNEL_NAME
    return SimpleNamespace(
        video_path=channel_dir / "video" / f"{stem}.mp4",
        thumb_path=channel_dir / "thumb" / f"{stem}.jpg",
        audio_path=channel_dir / "audio" / f"{stem}.mp3",
    )


def _video(video_id: str):
    return SimpleNamespace(
        video_id=video_id,
        title=f"Video {video_id}",
        sanitized_filename_base=video_id,
        display_order=1,
    )


def _path_text(value) -> str:
    return str(value).replace("\\", "/")


def _item_count(db_path: Path, video_id: str) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM download_items WHERE platform = ? AND channel_id = ? AND video_id = ?",
            (db_store.PLATFORM_YOUTUBE, CHANNEL_ID, video_id),
        ).fetchone()[0]


def _single_item_id(db_path: Path, video_id: str) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id FROM download_items WHERE platform = ? AND channel_id = ? AND video_id = ?",
            (db_store.PLATFORM_YOUTUBE, CHANNEL_ID, video_id),
        ).fetchall()
    _assert(len(rows) == 1, f"expected one item row for {video_id}, got {len(rows)}")
    return int(rows[0][0])


@contextmanager
def _patched_db(db_path: Path):
    old_state_db_file = state_store.db_file
    old_db_store_db_file = db_store.db_file
    try:
        state_store.db_file = lambda: db_path
        db_store.db_file = lambda: db_path
        yield
    finally:
        state_store.db_file = old_state_db_file
        db_store.db_file = old_db_store_db_file


@contextmanager
def _temp_runtime():
    with TemporaryDirectory(prefix="video_state_consistency_") as temp_dir:
        data_dir = Path(temp_dir) / "data"
        yield {"data_dir": data_dir, "db_path": data_dir / "download_state.sqlite3"}


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
