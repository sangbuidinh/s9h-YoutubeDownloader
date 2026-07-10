import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, downloader, state_store
from core.download_modes import MODE_VIDEO_AUDIO_THUMB, MODE_VIDEO_THUMB, PART_AUDIO, PART_THUMB, PART_VIDEO
from core.downloader import DownloadOptions


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
    _test_failed_media_replacement_preserves_existing_files()
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


def _test_failed_media_replacement_preserves_existing_files() -> None:
    with TemporaryDirectory(prefix="retry_media_replace_") as temp_dir:
        root = Path(temp_dir)
        staging = root / "stage"
        staging.mkdir()
        final_video = root / "video" / "video.mp4"
        final_audio = root / "audio" / "audio.mp3"
        final_thumb = root / "thumb" / "thumb.jpg"
        final_video.parent.mkdir()
        final_audio.parent.mkdir()
        final_thumb.parent.mkdir()
        final_video.write_bytes(b"OLD_VIDEO")
        final_audio.write_bytes(b"OLD_AUDIO")
        final_thumb.write_bytes(b"OLD_THUMB")

        old_run = downloader._run_ytdlp_with_retries
        old_ready = downloader._premiere_safe_mp4_ready_for_download
        old_validate = downloader._validate_premiere_safe_mp4_for_download
        old_promote = downloader._atomic_promote_with_retry
        try:
            def write_staged_mp4(command, _options, _log, _cancel_controller=None, _cookie_retry_state=None, **_kwargs):
                _output_path(command, "mp4").write_bytes(b"NEW_VIDEO")

            downloader._run_ytdlp_with_retries = write_staged_mp4
            downloader._premiere_safe_mp4_ready_for_download = lambda _path, _controller: False
            downloader._validate_premiere_safe_mp4_for_download = lambda *_args, **_kwargs: None
            downloader._atomic_promote_with_retry = lambda source, target, *_args, **_kwargs: (_ for _ in ()).throw(
                downloader.FileOperationError("promote", source, target, PermissionError("locked"))
            )
            try:
                downloader._download_video(
                    "retry-video",
                    "retry-video",
                    staging,
                    final_video,
                    DownloadOptions(str(root), CHANNEL_ID, CHANNEL_NAME),
                    lambda _message: None,
                )
            except downloader.FileOperationError:
                pass
            else:
                raise AssertionError("failed video replacement did not propagate")
        finally:
            downloader._run_ytdlp_with_retries = old_run
            downloader._premiere_safe_mp4_ready_for_download = old_ready
            downloader._validate_premiere_safe_mp4_for_download = old_validate
            downloader._atomic_promote_with_retry = old_promote

        _assert(final_audio.read_bytes() == b"OLD_AUDIO", "failed video replacement changed existing MP3")
        _assert(final_thumb.read_bytes() == b"OLD_THUMB", "failed video replacement changed existing thumbnail")

        old_run = downloader._run_ytdlp_with_retries
        old_ready_file = downloader._final_file_ready
        old_promote = downloader._atomic_promote_with_retry
        try:
            def write_staged_mp3(command, _options, _log, _cancel_controller=None, _cookie_retry_state=None, **_kwargs):
                _output_path(command, "mp3").write_bytes(b"NEW_AUDIO")

            def final_ready(path):
                if Path(path) == final_audio:
                    return False
                return old_ready_file(path)

            downloader._run_ytdlp_with_retries = write_staged_mp3
            downloader._final_file_ready = final_ready
            downloader._atomic_promote_with_retry = lambda source, target, *_args, **_kwargs: (_ for _ in ()).throw(
                downloader.FileOperationError("promote", source, target, PermissionError("locked"))
            )
            try:
                downloader._download_audio(
                    "retry-audio",
                    "retry-audio",
                    staging,
                    final_audio,
                    DownloadOptions(str(root), CHANNEL_ID, CHANNEL_NAME),
                    lambda _message: None,
                )
            except downloader.FileOperationError:
                pass
            else:
                raise AssertionError("failed audio replacement did not propagate")
        finally:
            downloader._run_ytdlp_with_retries = old_run
            downloader._final_file_ready = old_ready_file
            downloader._atomic_promote_with_retry = old_promote

        _assert(final_audio.read_bytes() == b"OLD_AUDIO", "failed audio replacement changed existing MP3")


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


def _output_path(command: list[str], extension: str) -> Path:
    template = Path(command[command.index("-o") + 1])
    return template.with_name(template.name.replace("%(ext)s", extension))


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
