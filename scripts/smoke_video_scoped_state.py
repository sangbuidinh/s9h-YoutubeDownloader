import sys
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, downloader, state_store
from core.download_modes import MODE_VIDEO_THUMB, PART_THUMB, PART_VIDEO, required_parts
from core.file_status import apply_statuses


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"
VIDEO_ID = "same-video"
FOLDER_A = "D:/A"
FOLDER_B = "D:/B"


def main() -> int:
    _configure_stdio()
    _test_downloaded_video_status_is_global()
    _test_downloader_skip_is_video_level()
    _test_manual_status_is_video_level()
    _test_redownload_clears_manual_override_globally()
    _test_duplicate_folder_rows_aggregate_to_one_video()
    print("video-scoped state smoke tests passed")
    return 0


def _test_downloaded_video_status_is_global() -> None:
    with _temp_runtime() as paths:
        with _patched_db_file(paths["db_path"]):
            _seed_downloaded(FOLDER_A)

            entry = state_store.get_video_entry(CHANNEL_ID, VIDEO_ID)
            _assert(
                state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_DOWNLOADED,
                "downloaded state was not readable without folder",
            )

            no_folder_video = _video()
            folder_b_video = _video()
            apply_statuses([no_folder_video], "", CHANNEL_NAME, CHANNEL_ID, download_mode=MODE_VIDEO_THUMB)
            apply_statuses([folder_b_video], FOLDER_B, CHANNEL_NAME, CHANNEL_ID, download_mode=MODE_VIDEO_THUMB)
            _assert(no_folder_video.status == state_store.STATUS_DOWNLOADED, "empty Save folder did not show downloaded")
            _assert(folder_b_video.status == state_store.STATUS_DOWNLOADED, "different Save folder did not show downloaded")


def _test_downloader_skip_is_video_level() -> None:
    with _temp_runtime() as paths:
        with _patched_db_file(paths["db_path"]):
            _seed_downloaded(FOLDER_A)
            logs: list[str] = []
            calls = {"video": 0, "thumb": 0}
            with _patched_downloader_transfers(calls):
                downloader.download_items(
                    [_video()],
                    _download_options(str(paths["folder_b"])),
                    logs.append,
                    lambda _video_arg: None,
                    cancel_controller=downloader.DownloadController(),
                )

            _assert(
                "[SKIP] same-video marked as downloaded in SQLite state" in logs,
                "download_items did not skip globally downloaded video",
            )
            _assert(calls == {"video": 0, "thumb": 0}, "download functions ran for skipped video")


def _test_manual_status_is_video_level() -> None:
    with _temp_runtime() as paths:
        with _patched_db_file(paths["db_path"]):
            _seed_downloaded(FOLDER_A)
            state_store.update_manual_status(
                CHANNEL_ID,
                CHANNEL_NAME,
                "",
                _video(),
                state_store.STATUS_NOT_DOWNLOADED,
            )
            _assert(_count_items(paths["db_path"]) == 1, "manual status created a separate folder row")

            entry = state_store.get_video_entry(CHANNEL_ID, VIDEO_ID)
            _assert(entry.get("manual_override") is True, "manual override was not applied globally")
            _assert(
                state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_NOT_DOWNLOADED,
                "manual not-downloaded status was not global",
            )
            _assert(
                set(state_store.missing_parts_for_mode(entry, MODE_VIDEO_THUMB)) == set(required_parts(MODE_VIDEO_THUMB)),
                "manual not-downloaded did not expose required parts",
            )

            for folder in ("", FOLDER_A, FOLDER_B):
                video = _video()
                apply_statuses([video], folder, CHANNEL_NAME, CHANNEL_ID, download_mode=MODE_VIDEO_THUMB)
                _assert(video.status == state_store.STATUS_NOT_DOWNLOADED, f"manual status was not global for {folder!r}")


def _test_redownload_clears_manual_override_globally() -> None:
    with _temp_runtime() as paths:
        with _patched_db_file(paths["db_path"]):
            _seed_downloaded(FOLDER_A)
            state_store.update_manual_status(
                CHANNEL_ID,
                CHANNEL_NAME,
                "",
                _video(),
                state_store.STATUS_NOT_DOWNLOADED,
            )
            _assert(_count_items(paths["db_path"]) == 1, "manual status before re-download created a separate row")

            calls = {"video": 0, "thumb": 0}
            with _patched_downloader_transfers(calls):
                downloader.download_items(
                    [_video()],
                    _download_options(str(paths["folder_b"])),
                    lambda _message: None,
                    lambda _video_arg: None,
                    cancel_controller=downloader.DownloadController(),
                )

            entry = state_store.get_video_entry(CHANNEL_ID, VIDEO_ID)
            _assert(_count_items(paths["db_path"]) == 1, "re-download created a separate folder row")
            _assert("manual_override" not in entry, "re-download did not clear manual override globally")
            _assert(
                state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_DOWNLOADED,
                "re-download did not restore downloaded status globally",
            )
            _assert(calls == {"video": 1, "thumb": 1}, "re-download did not run required fake downloads")


def _test_duplicate_folder_rows_aggregate_to_one_video() -> None:
    with _temp_runtime() as paths:
        _seed_duplicate_rows(paths["db_path"])
        with _patched_db_file(paths["db_path"]):
            entry = state_store.get_video_entry(CHANNEL_ID, VIDEO_ID)
            entries = state_store.get_channel_video_entries(CHANNEL_ID)

        _assert(
            state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_DOWNLOADED,
            "duplicate folder rows did not aggregate to downloaded",
        )
        _assert(list(entries) == [VIDEO_ID], "channel entries did not collapse duplicate rows to video_id")
        _assert(
            state_store.get_effective_status(entries[VIDEO_ID], MODE_VIDEO_THUMB) == state_store.STATUS_DOWNLOADED,
            "channel aggregate entry was not downloaded",
        )


def _seed_downloaded(save_base_folder: str) -> None:
    video = _video()
    paths = _paths_for(save_base_folder, video.sanitized_filename_base)
    for part in (PART_VIDEO, PART_THUMB):
        state_store.update_video_part_state(
            CHANNEL_ID,
            CHANNEL_NAME,
            save_base_folder,
            video,
            paths,
            part,
            state_store.STATUS_DOWNLOADED,
            MODE_VIDEO_THUMB,
        )


def _seed_duplicate_rows(db_path: Path) -> None:
    db_store.init_db(db_path)
    now = "2026-01-01T00:00:00+00:00"
    with closing(db_store.connect_db(db_path)) as conn:
        channel_a = _insert_channel(conn, FOLDER_A, now)
        channel_blank = _insert_channel(conn, "", now)
        item_a = _insert_item(conn, channel_a, FOLDER_A, state_store.STATUS_DOWNLOADED, now)
        _insert_file(conn, item_a, PART_VIDEO, state_store.STATUS_DOWNLOADED, "same-video.mp4", "D:/A/Channel/video/same-video.mp4", now)
        _insert_file(conn, item_a, PART_THUMB, state_store.STATUS_DOWNLOADED, "same-video.jpg", "D:/A/Channel/thumb/same-video.jpg", now)
        _insert_item(conn, channel_blank, "", state_store.STATUS_NOT_DOWNLOADED, now)
        conn.commit()


def _insert_channel(conn, folder: str, now: str) -> int:
    return conn.execute(
        """
        INSERT INTO channels(
            platform,
            channel_id,
            channel_name,
            save_base_folder_raw,
            save_base_folder_norm,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("youtube", CHANNEL_ID, CHANNEL_NAME, folder, _norm(folder), now, now),
    ).lastrowid


def _insert_item(conn, channel_db_id: int, folder: str, status: str, now: str) -> int:
    return conn.execute(
        """
        INSERT INTO download_items(
            channel_db_id,
            platform,
            channel_id,
            video_id,
            save_base_folder_raw,
            save_base_folder_norm,
            sanitized_filename_base,
            status,
            updated_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (channel_db_id, "youtube", CHANNEL_ID, VIDEO_ID, folder, _norm(folder), "same-video", status, now, now),
    ).lastrowid


def _insert_file(conn, item_id: int, part: str, status: str, filename: str, path: str, now: str) -> None:
    conn.execute(
        """
        INSERT INTO download_files(
            item_id,
            part,
            status,
            filename_raw,
            filename_norm,
            path_raw,
            path_norm,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (item_id, part, status, filename, filename.casefold(), path, _norm(path), now, now),
    )


def _count_items(db_path: Path) -> int:
    with closing(db_store.connect_db(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM download_items").fetchone()[0]


@contextmanager
def _patched_downloader_transfers(calls: dict[str, int]):
    old_download_video = downloader._download_video
    old_download_thumbnail = downloader._download_thumbnail
    old_log_runtime_tool_summary = downloader._log_runtime_tool_summary
    old_validate_download_environment = downloader.validate_download_environment

    def fake_download_video(_video_id, _stem, _temp_dir, final_path, _options, _log, _cancel_controller=None):
        calls["video"] += 1
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"mp4")

    def fake_download_thumbnail(_video, _stem, _temp_dir, final_path, _options, _log, _cancel_controller=None):
        calls["thumb"] += 1
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"jpg")

    try:
        downloader._download_video = fake_download_video
        downloader._download_thumbnail = fake_download_thumbnail
        downloader._log_runtime_tool_summary = lambda _log: None
        downloader.validate_download_environment = lambda _options: None
        yield
    finally:
        downloader._download_video = old_download_video
        downloader._download_thumbnail = old_download_thumbnail
        downloader._log_runtime_tool_summary = old_log_runtime_tool_summary
        downloader.validate_download_environment = old_validate_download_environment


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


@contextmanager
def _temp_runtime():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        folder_b = root / "Folder B"
        folder_b.mkdir(parents=True)
        yield {
            "db_path": root / "download_state.sqlite3",
            "folder_b": folder_b,
        }


def _download_options(base_folder: str) -> downloader.DownloadOptions:
    return downloader.DownloadOptions(
        base_folder=base_folder,
        channel_id=CHANNEL_ID,
        channel_name=CHANNEL_NAME,
        download_mode=MODE_VIDEO_THUMB,
    )


def _paths_for(base_folder: str, stem: str):
    channel_dir = Path(base_folder) / CHANNEL_NAME
    return SimpleNamespace(
        video_path=channel_dir / "video" / f"{stem}.mp4",
        thumb_path=channel_dir / "thumb" / f"{stem}.jpg",
        audio_path=channel_dir / "audio" / f"{stem}.mp3",
    )


def _video():
    return SimpleNamespace(
        video_id=VIDEO_ID,
        title="Same Video",
        sanitized_filename_base="same-video",
        thumbnail_url="",
        display_order=1,
        status=state_store.STATUS_NOT_DOWNLOADED,
    )


def _norm(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.endswith("/") and len(text) > 1:
        text = text[:-1]
    return text.casefold()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
