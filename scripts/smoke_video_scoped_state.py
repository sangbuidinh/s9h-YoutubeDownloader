import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, downloader, state_store
from core.download_modes import (
    MODE_VIDEO_AUDIO_THUMB,
    MODE_VIDEO_THUMB,
    PART_AUDIO,
    PART_THUMB,
    PART_VIDEO,
    required_parts,
)
from core.file_status import apply_statuses


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"
VIDEO_ID = "same-video"
FOLDER_A = "D:/A"
FOLDER_B = "D:/B"


def main() -> int:
    _configure_stdio()
    _test_downloaded_video_status_is_global()
    _test_downloader_skips_downloaded_across_number_and_folder_changes()
    _test_downloaded_skip_precedes_output_ownership()
    _test_selected_row_numbering_survives_skip()
    _test_manual_status_is_video_level()
    _test_redownload_clears_manual_override_globally()
    _test_manual_missing_audio_remains_part_scoped()
    _test_v3_duplicate_folder_rows_migrate_to_one_video()
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


def _test_downloader_skips_downloaded_across_number_and_folder_changes() -> None:
    for engine in (downloader.DOWNLOAD_ENGINE_STABLE, downloader.DOWNLOAD_ENGINE_ARIA2_FAST):
        for mode in (MODE_VIDEO_THUMB, MODE_VIDEO_AUDIO_THUMB):
            with _temp_runtime() as paths:
                with _patched_db_file(paths["db_path"]):
                    old_paths = _seed_downloaded(
                        str(paths["folder_a"]),
                        mode=mode,
                        stem="001 Same Video",
                    )
                    old_bytes = _write_old_outputs(old_paths, mode)
                    logs: list[str] = []
                    calls = _empty_calls()
                    statuses: list[str] = []
                    with _patched_downloader_transfers(calls):
                        downloader.download_items(
                            [_video()],
                            _download_options(
                                str(paths["folder_b"]),
                                start_number=51,
                                engine=engine,
                                mode=mode,
                            ),
                            logs.append,
                            lambda video_arg: statuses.append(video_arg.status),
                            cancel_controller=downloader.DownloadController(),
                        )

                    entry = state_store.get_video_entry(CHANNEL_ID, VIDEO_ID)
                    new_paths = _paths_for(str(paths["folder_b"]), "051 Same Video")
                    _assert(
                        any("[SKIP] same-video marked as downloaded in SQLite state" in line for line in logs),
                        f"{engine}/{mode} did not log the video-scoped skip",
                    )
                    _assert(calls == _empty_calls(), f"{engine}/{mode} invoked transfer/runtime work: {calls}")
                    _assert(_count_items(paths["db_path"]) == 1, f"{engine}/{mode} created another video row")
                    _assert(
                        state_store.get_effective_status(entry, mode) == state_store.STATUS_DOWNLOADED,
                        f"{engine}/{mode} downgraded downloaded state",
                    )
                    _assert(statuses and statuses[-1] == state_store.STATUS_DOWNLOADED, f"{engine}/{mode} callback status changed")
                    _assert(_read_outputs(old_paths, mode) == old_bytes, f"{engine}/{mode} changed old outputs")
                    _assert(not any(path.exists() for path in _part_paths(new_paths, mode)), f"{engine}/{mode} created new outputs")


def _test_downloaded_skip_precedes_output_ownership() -> None:
    with _temp_runtime() as paths:
        with _patched_db_file(paths["db_path"]):
            _seed_downloaded(str(paths["folder_a"]), stem="001 Same Video")
            target_paths = _paths_for(str(paths["folder_b"]), "051 Same Video")
            _seed_downloaded(
                str(paths["folder_b"]),
                video=_video("other-video", "Other Video"),
                stem="051 Same Video",
            )
            logs: list[str] = []
            calls = _empty_calls()
            with _patched_downloader_transfers(calls):
                downloader.download_items(
                    [_video()],
                    _download_options(str(paths["folder_b"]), start_number=51),
                    logs.append,
                    lambda _video_arg: None,
                    cancel_controller=downloader.DownloadController(),
                )

            ownership_errors = [line for line in logs if line.startswith("[ERROR]")]
            _assert(not ownership_errors, f"downloaded skip reached output ownership: {ownership_errors}")
            _assert(
                any("[SKIP] same-video marked as downloaded in SQLite state" in line for line in logs),
                "downloaded item was not skipped before ownership",
            )
            _assert(calls == _empty_calls(), f"ownership scenario invoked work for skipped video: {calls}")
            _assert(not any(path.exists() for path in _part_paths(target_paths, MODE_VIDEO_THUMB)), "skip created owned outputs")


def _test_selected_row_numbering_survives_skip() -> None:
    with _temp_runtime() as paths:
        with _patched_db_file(paths["db_path"]):
            first = _video("first-video", "First Video")
            second = _video("second-video", "Second Video")
            _seed_downloaded(
                str(paths["folder_a"]),
                video=first,
                stem="001 First Video",
            )
            logs: list[str] = []
            calls = _empty_calls()
            with _patched_downloader_transfers(calls):
                downloader.download_items(
                    [first, second],
                    _download_options(str(paths["folder_b"]), start_number=101),
                    logs.append,
                    lambda _video_arg: None,
                    cancel_controller=downloader.DownloadController(),
                )

            second_paths = _paths_for(str(paths["folder_b"]), "102 Second Video")
            compacted_paths = _paths_for(str(paths["folder_b"]), "101 Second Video")
            _assert(second_paths.video_path.exists(), "actual download did not retain selected-row number 102")
            _assert(second_paths.thumb_path.exists(), "actual thumbnail did not retain selected-row number 102")
            _assert(not compacted_paths.video_path.exists(), "download numbering compacted after a skipped row")
            _assert(not compacted_paths.thumb_path.exists(), "thumbnail numbering compacted after a skipped row")
            _assert(calls["video"] == 1 and calls["thumb"] == 1, f"unexpected selected-row transfer calls: {calls}")
            _assert("[INFO] Assigned output number: 101" in logs, "skipped row did not consume number 101")
            _assert("[INFO] Assigned output number: 102" in logs, "downloaded row did not retain number 102")


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
            _seed_downloaded(str(paths["folder_a"]), stem="001 Same Video")
            state_store.update_manual_status(
                CHANNEL_ID,
                CHANNEL_NAME,
                "",
                _video(),
                state_store.STATUS_NOT_DOWNLOADED,
            )
            _assert(_count_items(paths["db_path"]) == 1, "manual status before re-download created a separate row")

            calls = _empty_calls()
            with _patched_downloader_transfers(calls):
                downloader.download_items(
                    [_video()],
                    _download_options(str(paths["folder_b"]), start_number=51),
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
            _assert(calls["video"] == 1, "manual reset did not re-download video")
            _assert(calls["thumb"] == 1, "manual reset did not re-download thumbnail")
            _assert(calls["audio"] == 0, "manual video/thumb reset unexpectedly downloaded audio")
            _assert(calls["metadata"] == 1, "manual reset did not enter the normal video attempt path")
            _assert(calls["runtime"] == 1 and calls["prepare"] == 1, "manual reset skipped normal runtime setup")


def _test_manual_missing_audio_remains_part_scoped() -> None:
    with _temp_runtime() as paths:
        with _patched_db_file(paths["db_path"]):
            current_paths = _seed_downloaded(
                str(paths["folder_b"]),
                mode=MODE_VIDEO_AUDIO_THUMB,
                stem="051 Same Video",
            )
            current_paths.video_path.parent.mkdir(parents=True, exist_ok=True)
            current_paths.video_path.write_bytes(b"ready mp4")
            current_paths.thumb_path.parent.mkdir(parents=True, exist_ok=True)
            current_paths.thumb_path.write_bytes(b"ready jpg")
            if current_paths.audio_path.exists():
                current_paths.audio_path.unlink()
            state_store.update_manual_status(
                CHANNEL_ID,
                CHANNEL_NAME,
                str(paths["folder_b"]),
                _video(),
                state_store.STATUS_MISSING_AUDIO,
            )

            calls = _empty_calls()
            with _patched_downloader_transfers(calls):
                downloader.download_items(
                    [_video()],
                    _download_options(
                        str(paths["folder_b"]),
                        start_number=51,
                        mode=MODE_VIDEO_AUDIO_THUMB,
                    ),
                    lambda _message: None,
                    lambda _video_arg: None,
                    cancel_controller=downloader.DownloadController(),
                )

            entry = state_store.get_video_entry(CHANNEL_ID, VIDEO_ID)
            _assert(calls["video"] == 0, f"manual missing audio re-downloaded video: {calls}")
            _assert(calls["thumb"] == 0, f"manual missing audio re-downloaded thumbnail: {calls}")
            _assert(calls["audio"] == 1, f"manual missing audio did not run audio behavior: {calls}")
            _assert(
                state_store.get_effective_status(entry, MODE_VIDEO_AUDIO_THUMB) == state_store.STATUS_DOWNLOADED,
                "manual missing audio did not reconcile to downloaded",
            )


def _test_v3_duplicate_folder_rows_migrate_to_one_video() -> None:
    with _temp_runtime() as paths:
        _seed_duplicate_rows(paths["db_path"])
        with _patched_db_file(paths["db_path"]):
            entry = state_store.get_video_entry(CHANNEL_ID, VIDEO_ID)
            entries = state_store.get_channel_video_entries(CHANNEL_ID)

        _assert(_count_items(paths["db_path"]) == 1, "schema-v3 duplicate rows did not migrate to one physical row")
        _assert(
            state_store.get_effective_status(entry, MODE_VIDEO_THUMB) == state_store.STATUS_DOWNLOADED,
            "migrated duplicate folder rows did not produce downloaded state",
        )
        _assert(list(entries) == [VIDEO_ID], "channel entries did not expose one logical video_id")
        _assert(
            state_store.get_effective_status(entries[VIDEO_ID], MODE_VIDEO_THUMB) == state_store.STATUS_DOWNLOADED,
            "channel entry after migration was not downloaded",
        )


def _seed_downloaded(
    save_base_folder: str,
    *,
    mode: str = MODE_VIDEO_THUMB,
    video=None,
    stem: str | None = None,
):
    video = video or _video()
    paths = _paths_for(save_base_folder, stem or video.sanitized_filename_base)
    for part in required_parts(mode):
        state_store.update_video_part_state(
            CHANNEL_ID,
            CHANNEL_NAME,
            save_base_folder,
            video,
            paths,
            part,
            state_store.STATUS_DOWNLOADED,
            mode,
        )
    return paths


def _seed_duplicate_rows(db_path: Path) -> None:
    _create_v3_database(db_path)
    now = "2026-01-01T00:00:00+00:00"
    with closing(sqlite3.connect(db_path)) as conn:
        channel_a = _insert_channel(conn, FOLDER_A, now)
        channel_blank = _insert_channel(conn, "", now)
        item_a = _insert_item(conn, channel_a, FOLDER_A, state_store.STATUS_DOWNLOADED, now)
        _insert_file(conn, item_a, PART_VIDEO, state_store.STATUS_DOWNLOADED, "same-video.mp4", "D:/A/Channel/video/same-video.mp4", now)
        _insert_file(conn, item_a, PART_THUMB, state_store.STATUS_DOWNLOADED, "same-video.jpg", "D:/A/Channel/thumb/same-video.jpg", now)
        _insert_item(conn, channel_blank, "", state_store.STATUS_NOT_DOWNLOADED, now)
        conn.commit()


def _create_v3_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    now = "2026-01-01T00:00:00+00:00"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE app_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE channels (
                id INTEGER PRIMARY KEY,
                platform TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                channel_name TEXT NULL,
                save_base_folder_raw TEXT NULL,
                save_base_folder_norm TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(platform, channel_id, save_base_folder_norm)
            );
            CREATE TABLE download_items (
                id INTEGER PRIMARY KEY,
                channel_db_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                platform TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                save_base_folder_raw TEXT NULL,
                save_base_folder_norm TEXT NOT NULL,
                original_title TEXT NULL,
                sanitized_filename_base TEXT NOT NULL,
                display_order_at_download INTEGER NULL,
                status TEXT NULL,
                manual_status TEXT NULL,
                manual_override INTEGER NULL CHECK(manual_override IN (0, 1) OR manual_override IS NULL),
                downloaded_at TEXT NULL,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(platform, channel_id, video_id, save_base_folder_norm),
                FOREIGN KEY(channel_db_id) REFERENCES channels(id) ON DELETE CASCADE
            );
            CREATE TABLE download_files (
                id INTEGER PRIMARY KEY,
                item_id INTEGER NOT NULL REFERENCES download_items(id) ON DELETE CASCADE,
                part TEXT NOT NULL CHECK(part IN ('video', 'thumb', 'audio')),
                status TEXT NULL,
                filename_raw TEXT NULL,
                filename_norm TEXT NULL,
                path_raw TEXT NULL,
                path_norm TEXT NULL,
                is_valid INTEGER NOT NULL DEFAULT 1 CHECK(is_valid IN (0, 1)),
                validation_reason TEXT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(item_id, part),
                FOREIGN KEY(item_id) REFERENCES download_items(id) ON DELETE CASCADE
            );
            CREATE INDEX idx_download_items_channel ON download_items(channel_db_id);
            CREATE INDEX idx_download_items_channel_folder ON download_items(platform, channel_id, save_base_folder_norm);
            CREATE INDEX idx_download_files_path_norm ON download_files(path_norm);
            """
        )
        conn.execute("INSERT INTO app_meta(key, value, updated_at) VALUES ('schema_version', '3', ?)", (now,))
        for version in (1, 2, 3):
            conn.execute(
                "INSERT INTO app_schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (version, db_store.APPLICATION_MIGRATION_NAMES[version], now),
            )
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
    old_extract_mp3_from_video = downloader._extract_mp3_from_video
    old_video_attempt_state_for_batch = downloader._video_attempt_state_for_batch
    old_call_runtime_tool_summary = downloader._call_runtime_tool_summary
    old_prepare_media_downloader_runtime = downloader._prepare_media_downloader_runtime
    old_premiere_safe_ready = downloader._premiere_safe_mp4_ready_for_download
    old_validate_download_environment = downloader.validate_download_environment

    def fake_download_video(
        _video_id,
        _stem,
        _temp_dir,
        final_path,
        _options,
        _log,
        _cancel_controller=None,
        _cookie_retry_state=None,
        _aria2_validation=None,
    ):
        calls["video"] += 1
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"mp4")

    def fake_download_thumbnail(
        _video,
        _stem,
        _temp_dir,
        final_path,
        _options,
        _log,
        _cancel_controller=None,
        _cookie_retry_state=None,
    ):
        calls["thumb"] += 1
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"jpg")

    def fake_extract_mp3_from_video(
        _video_path,
        _temp_dir,
        final_path,
        _log,
        _cancel_controller=None,
    ):
        calls["audio"] += 1
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"mp3")

    def fake_video_attempt_state_for_batch(*_args, **_kwargs):
        calls["metadata"] += 1
        return downloader._YtdlpAttemptState(), None

    def fake_call_runtime_tool_summary(*_args, **_kwargs):
        calls["runtime"] += 1

    def fake_prepare_media_downloader_runtime(options, _log, _cancel_controller=None):
        calls["prepare"] += 1
        enabled = options.download_engine == downloader.DOWNLOAD_ENGINE_ARIA2_FAST
        return downloader._Aria2RuntimeValidation(enabled, enabled, Path("aria2c.exe"))

    try:
        downloader._download_video = fake_download_video
        downloader._download_thumbnail = fake_download_thumbnail
        downloader._extract_mp3_from_video = fake_extract_mp3_from_video
        downloader._video_attempt_state_for_batch = fake_video_attempt_state_for_batch
        downloader._call_runtime_tool_summary = fake_call_runtime_tool_summary
        downloader._prepare_media_downloader_runtime = fake_prepare_media_downloader_runtime
        downloader._premiere_safe_mp4_ready_for_download = lambda path, _cancel=None: path.exists()
        downloader.validate_download_environment = lambda _options: None
        yield
    finally:
        downloader._download_video = old_download_video
        downloader._download_thumbnail = old_download_thumbnail
        downloader._extract_mp3_from_video = old_extract_mp3_from_video
        downloader._video_attempt_state_for_batch = old_video_attempt_state_for_batch
        downloader._call_runtime_tool_summary = old_call_runtime_tool_summary
        downloader._prepare_media_downloader_runtime = old_prepare_media_downloader_runtime
        downloader._premiere_safe_mp4_ready_for_download = old_premiere_safe_ready
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
        folder_a = root / "Folder A"
        folder_b = root / "Folder B"
        folder_a.mkdir(parents=True)
        folder_b.mkdir(parents=True)
        yield {
            "db_path": root / "download_state.sqlite3",
            "folder_a": folder_a,
            "folder_b": folder_b,
        }


def _download_options(
    base_folder: str,
    *,
    start_number: int = 1,
    engine: str = downloader.DOWNLOAD_ENGINE_STABLE,
    mode: str = MODE_VIDEO_THUMB,
) -> downloader.DownloadOptions:
    return downloader.DownloadOptions(
        base_folder=base_folder,
        channel_id=CHANNEL_ID,
        channel_name=CHANNEL_NAME,
        download_mode=mode,
        download_engine=engine,
        file_start_number=start_number,
    )


def _paths_for(base_folder: str, stem: str):
    channel_dir = Path(base_folder) / CHANNEL_NAME
    return SimpleNamespace(
        video_path=channel_dir / "video" / f"{stem}.mp4",
        thumb_path=channel_dir / "thumb" / f"{stem}.jpg",
        audio_path=channel_dir / "audio" / f"{stem}.mp3",
    )


def _video(video_id: str = VIDEO_ID, title: str = "Same Video"):
    return SimpleNamespace(
        video_id=video_id,
        title=title,
        sanitized_filename_base=video_id,
        thumbnail_url="",
        display_order=1,
        status=state_store.STATUS_NOT_DOWNLOADED,
    )


def _part_paths(paths, mode: str) -> tuple[Path, ...]:
    path_by_part = {
        PART_VIDEO: paths.video_path,
        PART_AUDIO: paths.audio_path,
        PART_THUMB: paths.thumb_path,
    }
    return tuple(path_by_part[part] for part in required_parts(mode))


def _write_old_outputs(paths, mode: str) -> dict[str, bytes]:
    contents = {
        PART_VIDEO: b"old mp4",
        PART_AUDIO: b"old mp3",
        PART_THUMB: b"old jpg",
    }
    path_by_part = {
        PART_VIDEO: paths.video_path,
        PART_AUDIO: paths.audio_path,
        PART_THUMB: paths.thumb_path,
    }
    written: dict[str, bytes] = {}
    for part in required_parts(mode):
        path = path_by_part[part]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents[part])
        written[part] = contents[part]
    return written


def _read_outputs(paths, mode: str) -> dict[str, bytes]:
    path_by_part = {
        PART_VIDEO: paths.video_path,
        PART_AUDIO: paths.audio_path,
        PART_THUMB: paths.thumb_path,
    }
    return {part: path_by_part[part].read_bytes() for part in required_parts(mode)}


def _empty_calls() -> dict[str, int]:
    return {
        "video": 0,
        "audio": 0,
        "thumb": 0,
        "metadata": 0,
        "runtime": 0,
        "prepare": 0,
    }


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
