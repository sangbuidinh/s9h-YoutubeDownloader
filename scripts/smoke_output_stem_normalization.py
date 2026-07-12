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
from core.filename_utils import (
    MAX_FILENAME_BASE_LENGTH,
    assign_unique_title_bases,
    normalize_output_stem,
    sanitize_video_filename_base,
    strip_known_media_suffixes,
)


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"
SAVE_BASE_FOLDER = "D:/A"


def main() -> int:
    _configure_stdio()
    real_runtime_before = _snapshot_real_runtime_files()
    _test_normalization_examples()
    _test_filename_sanitization_hardening()
    _test_case_insensitive_filename_collisions()
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


def _test_filename_sanitization_hardening() -> None:
    cases = {
        "line\nbreak": "line_break",
        "tab\tvalue": "tab_value",
        "nul\x00value": "nul_value",
        "unit\x1fseparator": "unit_separator",
        "delete\x7fvalue": "delete_value",
        'bad\\/:*?"<>|name': "bad_________name",
        "CON": "CON_",
        "trailing. ": "trailing",
        "\x00\x1f\x7f": "___",
    }
    for value, expected in cases.items():
        actual = sanitize_video_filename_base(value)
        _assert(actual == expected, f"sanitize failed for {value!r}: {actual!r}")
        _assert_filename_characters_are_safe(actual)

    long_value = "A" * (MAX_FILENAME_BASE_LENGTH + 25)
    trimmed = sanitize_video_filename_base(long_value)
    _assert(len(trimmed) == MAX_FILENAME_BASE_LENGTH, "sanitized filename exceeded max length")
    _assert(trimmed == "A" * MAX_FILENAME_BASE_LENGTH, "max-length trimming changed valid characters")
    _assert_filename_characters_are_safe(trimmed)


def _test_case_insensitive_filename_collisions() -> None:
    videos = [
        SimpleNamespace(title="Example"),
        SimpleNamespace(title="example"),
        SimpleNamespace(title="EXAMPLE"),
    ]
    assign_unique_title_bases(videos)
    actual = [video.sanitized_filename_base for video in videos]
    _assert(actual == ["Example", "example (2)", "EXAMPLE (3)"], f"collision suffixes were wrong: {actual}")
    _assert(len({value.casefold() for value in actual}) == len(actual), "case-insensitive collision remained")
    for value in actual:
        _assert_filename_characters_are_safe(value)


def _assert_filename_characters_are_safe(value: str) -> None:
    windows_invalid = set('\\/:*?"<>|')
    _assert(not any(ord(char) < 32 for char in value), f"control character remained in {value!r}")
    _assert("\x7f" not in value, f"DEL remained in {value!r}")
    _assert(not any(char in windows_invalid for char in value), f"Windows-invalid character remained in {value!r}")


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

    _assert(
        video.sanitized_filename_base == "001 Original YouTube Title",
        "downloader did not rebuild numbered stem from canonical title",
    )
    _assert(calls["video_path"].name == "001 Original YouTube Title.mp4", "redownload video path has wrong name")
    _assert(calls["thumb_path"].name == "001 Original YouTube Title.jpg", "redownload thumb path has wrong name")
    _assert(calls["video_path"].name != "sample.mp4.mp4", "redownload created duplicated video extension")
    _assert(calls["thumb_path"].name != "sample.mp4.jpg", "redownload created contaminated thumb extension")
    _assert(
        entry["sanitized_filename_base"] == "001 Original YouTube Title",
        "redownload saved contaminated sanitized_filename_base",
    )
    _assert(entry["video_filename"] == "001 Original YouTube Title.mp4", "redownload video filename is wrong")
    _assert(entry["thumb_filename"] == "001 Original YouTube Title.jpg", "redownload thumb filename is wrong")


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
