import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_store
from core.download_modes import MODE_VIDEO_THUMB, PART_THUMB, PART_VIDEO
from core.downloader import DownloadOptions, download_items
from core.file_status import build_output_paths
from core.filename_utils import normalize_output_stem, strip_known_media_suffixes


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"
SAVE_BASE_FOLDER = "D:/A"


def main() -> int:
    _configure_stdio()
    real_runtime_before = _snapshot_real_runtime_files()
    _test_normalization_examples()
    _test_build_output_paths_are_extensionless()
    _test_sqlite_sanitized_filename_base_is_extensionless()
    _test_manual_status_redownload_uses_extensionless_stem()
    _assert(
        real_runtime_before == _snapshot_real_runtime_files(),
        "real runtime state files were mutated by temp-file smoke tests",
    )
    print("output stem normalization smoke tests passed")
    return 0


def _test_normalization_examples() -> None:
    cases = {
        "abc.mp4": "abc",
        "abc.mp4.mp4": "abc",
        "abc.mp4.jpg": "abc",
        "abc.jpg": "abc",
        "abc.JPG": "abc",
        "abc.mp3": "abc",
        "abc": "abc",
    }
    for value, expected in cases.items():
        _assert(strip_known_media_suffixes(value) == expected, f"strip failed for {value!r}")
        _assert(normalize_output_stem(value) == expected, f"normalize failed for {value!r}")


def _test_build_output_paths_are_extensionless() -> None:
    video = _video("stem-test", sanitized_filename_base="sample.mp4")
    raw_stem = getattr(video, "sanitized_filename_base", "") or video.title
    stem = normalize_output_stem(raw_stem)
    video.sanitized_filename_base = stem
    paths = build_output_paths("D:/Out", CHANNEL_NAME, stem)

    _assert(paths.video_path.name == "sample.mp4", "video path got contaminated stem")
    _assert(paths.thumb_path.name == "sample.jpg", "thumb path got contaminated stem")
    _assert(paths.audio_path.name == "sample.mp3", "audio path got contaminated stem")
    _assert(paths.video_path.name != "sample.mp4.mp4", "video path contains duplicated extension")
    _assert(paths.thumb_path.name != "sample.mp4.jpg", "thumb path contains contaminated extension")


def _test_sqlite_sanitized_filename_base_is_extensionless() -> None:
    with _temp_runtime() as paths:
        db_store.update_video_state(
            CHANNEL_ID,
            "state-stem",
            {
                "channel_name": CHANNEL_NAME,
                "original_title": "Original YouTube Title.mp4",
                "sanitized_filename_base": "sample.mp4",
                "status": state_store.STATUS_DOWNLOADED,
                "video_filename": "sample.mp4",
                "video_path": "D:/A/Channel/video/sample.mp4",
                "video_status": state_store.STATUS_DOWNLOADED,
            },
            path=paths["db_path"],
            save_base_folder=SAVE_BASE_FOLDER,
        )
        entry = db_store.get_video_entry(
            CHANNEL_ID,
            "state-stem",
            path=paths["db_path"],
            save_base_folder=SAVE_BASE_FOLDER,
        )

    _assert(entry["sanitized_filename_base"] == "sample", "SQLite saved contaminated sanitized_filename_base")
    _assert(entry["video_filename"] == "sample.mp4", "SQLite stripped extension from video_filename")
    _assert(entry["original_title"] == "Original YouTube Title.mp4", "original title was altered")


def _test_manual_status_redownload_uses_extensionless_stem() -> None:
    with _temp_runtime() as paths:
        with _patched_db_file(paths["db_path"]):
            video = _video("manual-redownload", sanitized_filename_base="sample.mp4")
            output_paths = build_output_paths(paths["data_dir"], CHANNEL_NAME, video.sanitized_filename_base)
            db_store.update_video_state(
                CHANNEL_ID,
                video.video_id,
                {
                    "channel_name": CHANNEL_NAME,
                    "original_title": video.title,
                    "sanitized_filename_base": video.sanitized_filename_base,
                    "status": state_store.STATUS_DOWNLOADED,
                    "video_filename": output_paths.video_path.name,
                    "thumb_filename": output_paths.thumb_path.name,
                    "video_path": str(output_paths.video_path),
                    "thumb_path": str(output_paths.thumb_path),
                    "video_status": state_store.STATUS_DOWNLOADED,
                    "thumb_status": state_store.STATUS_DOWNLOADED,
                },
                save_base_folder=SAVE_BASE_FOLDER,
            )
            db_store.update_manual_status(
                CHANNEL_ID,
                video.video_id,
                state_store.STATUS_NOT_DOWNLOADED,
                save_base_folder=SAVE_BASE_FOLDER,
            )

            calls = _patch_downloader_for_dry_run(paths["data_dir"])
            options = DownloadOptions(
                base_folder=str(paths["data_dir"]),
                channel_id=CHANNEL_ID,
                channel_name=CHANNEL_NAME,
                download_mode=MODE_VIDEO_THUMB,
                file_start_number=1,
            )
            download_items([video], options, lambda _message: None, lambda _video: None)
            entry = db_store.get_video_entry(CHANNEL_ID, video.video_id, save_base_folder=SAVE_BASE_FOLDER)

    _assert(video.sanitized_filename_base == "001 sample", "downloader did not normalize numbered in-memory stem")
    _assert(calls["video_path"].name == "001 sample.mp4", "redownload video path has wrong name")
    _assert(calls["thumb_path"].name == "001 sample.jpg", "redownload thumb path has wrong name")
    _assert(calls["video_path"].name != "sample.mp4.mp4", "redownload created duplicated video extension")
    _assert(calls["thumb_path"].name != "sample.mp4.jpg", "redownload created contaminated thumb extension")
    _assert(entry["sanitized_filename_base"] == "001 sample", "redownload saved contaminated sanitized_filename_base")
    _assert(entry["video_filename"] == "001 sample.mp4", "redownload video filename is wrong")
    _assert(entry["thumb_filename"] == "001 sample.jpg", "redownload thumb filename is wrong")


def _patch_downloader_for_dry_run(data_dir: Path) -> dict:
    import core.downloader as downloader

    calls = {}
    old_validate_download_environment = downloader.validate_download_environment
    old_download_video = downloader._download_video
    old_download_thumbnail = downloader._download_thumbnail

    def validate_download_environment(_options):
        return None

    def download_video(
        _video_id,
        _stem,
        _temp_path,
        final_path,
        _options,
        _log,
        _cancel_controller=None,
        _cookie_retry_state=None,
        _aria2_validation=None,
    ):
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"video")
        calls["video_path"] = final_path

    def download_thumbnail(
        _video,
        _stem,
        _temp_path,
        final_path,
        _options,
        _log,
        _cancel_controller=None,
        _cookie_retry_state=None,
    ):
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"thumb")
        calls["thumb_path"] = final_path

    downloader.validate_download_environment = validate_download_environment
    downloader._download_video = download_video
    downloader._download_thumbnail = download_thumbnail
    data_dir.mkdir(parents=True, exist_ok=True)

    class _Restore:
        def __del__(self):
            downloader.validate_download_environment = old_validate_download_environment
            downloader._download_video = old_download_video
            downloader._download_thumbnail = old_download_thumbnail

    calls["_restore"] = _Restore()
    return calls


def _video(video_id: str, sanitized_filename_base: str):
    return SimpleNamespace(
        video_id=video_id,
        title="Original YouTube Title",
        sanitized_filename_base=sanitized_filename_base,
        display_order=1,
    )


@contextmanager
def _temp_runtime():
    with TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir) / "data"
        yield {
            "data_dir": data_dir,
            "db_path": data_dir / "download_state.sqlite3",
        }


@contextmanager
def _patched_db_file(db_path: Path):
    old_db_file = db_store.db_file
    old_state_db_file = state_store.db_file
    try:
        db_store.db_file = lambda: db_path
        state_store.db_file = lambda: db_path
        yield
    finally:
        db_store.db_file = old_db_file
        state_store.db_file = old_state_db_file


def _snapshot_real_runtime_files() -> dict[str, tuple[bool, int | None, int | None]]:
    paths = {
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
