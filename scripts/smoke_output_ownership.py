import argparse
import hashlib
import multiprocessing
import os
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, downloader, state_store
from core.download_contracts import DownloadOptions
from core.download_modes import (
    MODE_VIDEO_AUDIO_THUMB,
    MODE_VIDEO_THUMB,
    PART_AUDIO,
    PART_THUMB,
    PART_VIDEO,
)
from core.file_status import build_output_paths, channel_dir_for
from core.output_ownership import (
    CHANNEL_OWNER_FILENAME,
    OutputOwnershipError,
    named_mutex,
    reserve_output_paths,
)


PROCESS_A_VIDEO_ID = "video-a"
PROCESS_B_VIDEO_ID = "video-b"
CHANNEL_ID_A = "UC_A"
CHANNEL_ID_B = "UC_B"
COLLIDING_CHANNEL_NAME_A = "ABC?"
COLLIDING_CHANNEL_NAME_B = "ABC*"
SHARED_STEM = "001 Example"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("all", "channel", "promotion"), default="all")
    args = parser.parse_args()
    _configure_stdio()

    if args.case in ("all", "channel"):
        _test_colliding_channel_ids_are_separated()
    if args.case in ("all", "promotion"):
        _test_two_processes_cannot_promote_different_videos_to_one_path()
    if args.case == "all":
        _test_same_channel_id_is_stable_across_processes()
        _test_different_owner_cannot_pass_final_promotion_guard()
        _test_same_video_retry_can_replace_owned_output()
        _test_existing_valid_item_short_circuits()
        _test_audio_and_thumbnail_ownership()
        _test_three_part_reservation_uses_one_stem()
        _test_abandoned_mutex_is_reacquired()
        _test_legacy_noncolliding_directory_is_reused()
        _test_disambiguated_paths_are_written_to_sqlite_state()
        _test_collision_rejection_does_not_mark_state_downloaded()
        _test_numbered_prefix_semantics_are_unchanged()

    print("output ownership smoke tests passed")
    return 0


def _test_colliding_channel_ids_are_separated() -> None:
    with TemporaryDirectory(prefix="bug01_channel_collision_") as temp_dir:
        channel_a = channel_dir_for(temp_dir, COLLIDING_CHANNEL_NAME_A, CHANNEL_ID_A)
        channel_b = channel_dir_for(temp_dir, COLLIDING_CHANNEL_NAME_B, CHANNEL_ID_B)
        paths_a = build_output_paths(temp_dir, COLLIDING_CHANNEL_NAME_A, SHARED_STEM, CHANNEL_ID_A)
        paths_b = build_output_paths(temp_dir, COLLIDING_CHANNEL_NAME_B, SHARED_STEM, CHANNEL_ID_B)

        print(f"CHANNEL_ID_A={CHANNEL_ID_A}")
        print(f"CHANNEL_ID_B={CHANNEL_ID_B}")
        print(f"CHANNEL_DIR_A={channel_a}")
        print(f"CHANNEL_DIR_B={channel_b}")
        print(f"FINAL_PATH_A={paths_a.video_path}")
        print(f"FINAL_PATH_B={paths_b.video_path}")

        _assert(CHANNEL_ID_A != CHANNEL_ID_B, "test fixture channel IDs unexpectedly match")
        _assert(channel_a != channel_b, "different channel IDs resolved to one sanitized channel namespace")
        _assert(paths_a.video_path != paths_b.video_path, "different channel IDs resolved to one final video path")


def _test_two_processes_cannot_promote_different_videos_to_one_path() -> None:
    context = multiprocessing.get_context("spawn")
    with TemporaryDirectory(prefix="bug01_process_collision_") as temp_dir:
        root = Path(temp_dir)
        final_path = root / "channel" / "video" / f"{SHARED_STEM}.mp4"
        source_a = root / "stage-a" / "candidate.mp4"
        source_b = root / "stage-b" / "candidate.mp4"
        source_a.parent.mkdir(parents=True)
        source_b.parent.mkdir(parents=True)
        source_a.write_bytes(b"PROCESS_A_CONTENT")
        source_b.write_bytes(b"PROCESS_B_CONTENT")
        source_a_hash = _sha256(source_a)
        source_b_hash = _sha256(source_b)

        ready_a = context.Event()
        ready_b = context.Event()
        start = context.Event()
        results = context.Queue()
        process_a = context.Process(
            target=_promotion_worker,
            args=(PROCESS_A_VIDEO_ID, source_a, final_path, ready_a, start, results),
        )
        process_b = context.Process(
            target=_promotion_worker,
            args=(PROCESS_B_VIDEO_ID, source_b, final_path, ready_b, start, results),
        )
        process_a.start()
        process_b.start()
        _assert(ready_a.wait(timeout=10), "process A did not reach the promotion barrier")
        _assert(ready_b.wait(timeout=10), "process B did not reach the promotion barrier")
        start.set()
        process_a.join(timeout=15)
        process_b.join(timeout=15)
        _assert(not process_a.is_alive() and not process_b.is_alive(), "promotion workers did not exit")

        outcomes = sorted((results.get(timeout=5), results.get(timeout=5)))
        successful = [outcome for outcome in outcomes if outcome[1] == "success"]
        rejected = [outcome for outcome in outcomes if outcome[1] == "rejected"]
        final_hash = _sha256(final_path)

        print(f"PROCESS_A_VIDEO_ID={PROCESS_A_VIDEO_ID}")
        print(f"PROCESS_B_VIDEO_ID={PROCESS_B_VIDEO_ID}")
        print(f"FINAL_PATH={final_path}")
        print(f"PROCESS_A_SOURCE_HASH={source_a_hash}")
        print(f"PROCESS_B_SOURCE_HASH={source_b_hash}")
        print(f"FINAL_HASH={final_hash}")
        print(f"PROCESS_OUTCOMES={outcomes}")

        _assert(len(successful) == 1, "different logical owners were both allowed to promote")
        _assert(len(rejected) == 1, "the second different logical owner was not rejected")


def _test_same_channel_id_is_stable_across_processes() -> None:
    context = multiprocessing.get_context("spawn")
    with TemporaryDirectory(prefix="bug01_same_channel_processes_") as temp_dir:
        ready_a = context.Event()
        ready_b = context.Event()
        start = context.Event()
        results = context.Queue()
        processes = (
            context.Process(
                target=_channel_resolution_worker,
                args=(temp_dir, ready_a, start, results),
            ),
            context.Process(
                target=_channel_resolution_worker,
                args=(temp_dir, ready_b, start, results),
            ),
        )
        for process in processes:
            process.start()
        _assert(ready_a.wait(timeout=10), "channel resolver A did not reach its barrier")
        _assert(ready_b.wait(timeout=10), "channel resolver B did not reach its barrier")
        start.set()
        for process in processes:
            process.join(timeout=15)
        _assert(all(not process.is_alive() for process in processes), "channel resolver workers did not exit")
        resolved = (results.get(timeout=5), results.get(timeout=5))
        _assert(resolved[0] == resolved[1], f"same channel ID resolved inconsistently: {resolved}")
        _assert((Path(resolved[0]) / CHANNEL_OWNER_FILENAME).is_file(), "channel owner marker was not persisted")


def _test_different_owner_cannot_pass_final_promotion_guard() -> None:
    with TemporaryDirectory(prefix="bug01_final_guard_") as temp_dir:
        root = Path(temp_dir)
        final_path = root / "channel" / "video" / f"{SHARED_STEM}.mp4"
        first_source = root / "first.mp4"
        second_source = root / "second.mp4"
        first_source.write_bytes(b"FIRST_OWNER")
        second_source.write_bytes(b"SECOND_OWNER")
        reservation = reserve_output_paths(
            final_path.parent.parent,
            "shared-channel",
            PROCESS_A_VIDEO_ID,
            {PART_VIDEO: final_path},
        )
        claim = reservation.claim_for_path(final_path)
        downloader._atomic_promote_with_retry(
            first_source,
            final_path,
            replace_existing=True,
            ownership_claim=claim,
        )
        foreign_claim = replace(claim, video_id=PROCESS_B_VIDEO_ID)
        _assert_raises(
            OutputOwnershipError,
            lambda: downloader._atomic_promote_with_retry(
                second_source,
                final_path,
                replace_existing=True,
                ownership_claim=foreign_claim,
            ),
            "different video owner passed the final promotion guard",
        )
        _assert(final_path.read_bytes() == b"FIRST_OWNER", "different video owner overwrote the final target")
        _assert(second_source.read_bytes() == b"SECOND_OWNER", "rejected staged file was destructively consumed")


def _test_same_video_retry_can_replace_owned_output() -> None:
    with TemporaryDirectory(prefix="bug01_same_video_retry_") as temp_dir:
        root = Path(temp_dir)
        final_path = root / "channel" / "video" / f"{SHARED_STEM}.mp4"
        first_source = root / "first.mp4"
        retry_source = root / "retry.mp4"
        first_source.write_bytes(b"FIRST_ATTEMPT")
        retry_source.write_bytes(b"REPAIRED_RETRY")
        first = reserve_output_paths(
            final_path.parent.parent,
            "shared-channel",
            PROCESS_A_VIDEO_ID,
            {PART_VIDEO: final_path},
        )
        downloader._atomic_promote_with_retry(
            first_source,
            final_path,
            replace_existing=True,
            ownership_claim=first.claim_for_path(final_path),
        )
        retry = reserve_output_paths(
            final_path.parent.parent,
            "shared-channel",
            PROCESS_A_VIDEO_ID,
            {PART_VIDEO: final_path},
        )
        downloader._atomic_promote_with_retry(
            retry_source,
            final_path,
            replace_existing=True,
            ownership_claim=retry.claim_for_path(final_path),
        )
        _assert(final_path.read_bytes() == b"REPAIRED_RETRY", "same-video replacement was blocked")


def _test_existing_valid_item_short_circuits() -> None:
    with TemporaryDirectory(prefix="bug01_existing_valid_") as temp_dir:
        root = Path(temp_dir)
        db_path = root / "state" / "download_state.sqlite3"
        base = root / "output"
        base.mkdir()
        video = _video(PROCESS_A_VIDEO_ID)
        options = _options(base, "shared-channel", "Channel", MODE_VIDEO_THUMB)
        with _patched_state_db(db_path):
            paths = build_output_paths(base, options.channel_name, SHARED_STEM, options.channel_id)
            reservation = reserve_output_paths(
                paths.channel_dir,
                options.channel_id,
                video.video_id,
                {PART_VIDEO: paths.video_path, PART_THUMB: paths.thumb_path},
            )
            _assert(len(reservation.claims) == 2, "valid-item fixture reservation was incomplete")
            paths.video_path.parent.mkdir(parents=True, exist_ok=True)
            paths.thumb_path.parent.mkdir(parents=True, exist_ok=True)
            paths.video_path.write_bytes(b"existing-video")
            paths.thumb_path.write_bytes(b"existing-thumb")
            for part in (PART_VIDEO, PART_THUMB):
                state_store.update_video_part_state(
                    options.channel_id,
                    options.channel_name,
                    options.base_folder,
                    video,
                    paths,
                    part,
                    state_store.STATUS_DOWNLOADED,
                    options.download_mode,
                )

            logs: list[str] = []
            with _patched_download_environment(), _transfers_must_not_run():
                downloader.download_items([video], options, logs.append, lambda _video: None)

            _assert(any("[SKIP]" in line for line in logs), "existing valid item did not short-circuit")
            _assert(paths.video_path.read_bytes() == b"existing-video", "existing valid video changed during skip")
            _assert(paths.thumb_path.read_bytes() == b"existing-thumb", "existing valid thumbnail changed during skip")


def _test_audio_and_thumbnail_ownership() -> None:
    with TemporaryDirectory(prefix="bug01_part_owners_") as temp_dir:
        root = Path(temp_dir)
        paths = build_output_paths(root, "Channel", SHARED_STEM, "shared-channel")
        reservation = reserve_output_paths(
            paths.channel_dir,
            "shared-channel",
            PROCESS_A_VIDEO_ID,
            {PART_AUDIO: paths.audio_path, PART_THUMB: paths.thumb_path},
        )
        for part, path, content in (
            (PART_AUDIO, paths.audio_path, b"owned-audio"),
            (PART_THUMB, paths.thumb_path, b"owned-thumb"),
        ):
            source = root / f"staged-{part}{path.suffix}"
            source.write_bytes(content)
            downloader._atomic_promote_with_retry(
                source,
                path,
                ownership_claim=reservation.claim_for_path(path),
            )
            _assert(path.read_bytes() == content, f"owned {part} output was not promoted")
            _assert_raises(
                OutputOwnershipError,
                lambda part=part, path=path: reserve_output_paths(
                    paths.channel_dir,
                    "shared-channel",
                    PROCESS_B_VIDEO_ID,
                    {part: path},
                ),
                f"different video claimed existing {part} output",
            )


def _test_three_part_reservation_uses_one_stem() -> None:
    with TemporaryDirectory(prefix="bug01_consistent_stem_") as temp_dir:
        paths = build_output_paths(temp_dir, "Channel", SHARED_STEM, "shared-channel")
        reservation = reserve_output_paths(
            paths.channel_dir,
            "shared-channel",
            PROCESS_A_VIDEO_ID,
            {
                PART_VIDEO: paths.video_path,
                PART_AUDIO: paths.audio_path,
                PART_THUMB: paths.thumb_path,
            },
        )
        _assert(len(reservation.claims) == 3, "three-part reservation was incomplete")
        _assert(
            {claim.final_path.stem for claim in reservation.claims} == {SHARED_STEM},
            "video, audio and thumbnail did not share one chosen stem",
        )


def _test_abandoned_mutex_is_reacquired() -> None:
    context = multiprocessing.get_context("spawn")
    with TemporaryDirectory(prefix="bug01_abandoned_mutex_") as temp_dir:
        resource = Path(temp_dir) / "resource"
        ready = context.Event()
        process = context.Process(target=_abandon_mutex_worker, args=(resource, ready))
        process.start()
        _assert(ready.wait(timeout=10), "abandoning worker did not acquire the mutex")
        process.join(timeout=10)
        _assert(not process.is_alive(), "abandoning worker did not exit")
        with named_mutex(resource, timeout_seconds=5):
            reacquired = True
        _assert(reacquired, "abandoned cross-process mutex was not reacquired")


def _test_legacy_noncolliding_directory_is_reused() -> None:
    with TemporaryDirectory(prefix="bug01_legacy_channel_") as temp_dir:
        root = Path(temp_dir)
        db_path = root / "state" / "download_state.sqlite3"
        base = root / "output"
        base.mkdir()
        legacy_paths = build_output_paths(base, "Legacy Channel", SHARED_STEM)
        legacy_paths.video_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_paths.video_path.write_bytes(b"legacy-video")
        video = _video(PROCESS_A_VIDEO_ID)
        with _patched_state_db(db_path):
            state_store.update_video_part_state(
                "legacy-channel-id",
                "Legacy Channel",
                str(base),
                video,
                legacy_paths,
                PART_VIDEO,
                state_store.STATUS_DOWNLOADED,
                MODE_VIDEO_THUMB,
            )
            foreign = channel_dir_for(base, "Legacy Channel", "foreign-channel-id")
            _assert(foreign != legacy_paths.channel_dir, "SQLite-owned legacy directory was reused by another channel")
            resolved = channel_dir_for(base, "Legacy Channel", "legacy-channel-id")
        _assert(resolved == legacy_paths.channel_dir, "known non-colliding legacy directory was renamed")
        _assert((resolved / CHANNEL_OWNER_FILENAME).is_file(), "legacy directory was not safely claimed")


def _test_disambiguated_paths_are_written_to_sqlite_state() -> None:
    with TemporaryDirectory(prefix="bug01_disambiguated_state_") as temp_dir:
        root = Path(temp_dir)
        db_path = root / "state" / "download_state.sqlite3"
        base = root / "output"
        base.mkdir()
        channel_dir_for(base, COLLIDING_CHANNEL_NAME_A, CHANNEL_ID_A)
        paths_b = build_output_paths(base, COLLIDING_CHANNEL_NAME_B, SHARED_STEM, CHANNEL_ID_B)
        video_b = _video(PROCESS_B_VIDEO_ID)
        with _patched_state_db(db_path):
            for part, path in (
                (PART_VIDEO, paths_b.video_path),
                (PART_AUDIO, paths_b.audio_path),
                (PART_THUMB, paths_b.thumb_path),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(part.encode("ascii"))
                state_store.update_video_part_state(
                    CHANNEL_ID_B,
                    COLLIDING_CHANNEL_NAME_B,
                    str(base),
                    video_b,
                    paths_b,
                    part,
                    state_store.STATUS_DOWNLOADED,
                    MODE_VIDEO_AUDIO_THUMB,
                )
            entry = state_store.get_video_entry(CHANNEL_ID_B, PROCESS_B_VIDEO_ID)
        _assert(paths_b.channel_dir.name != "ABC_", "colliding channel was not disambiguated")
        _assert(entry is not None, "disambiguated channel state was not stored")
        _assert(entry.get("video_path") == str(paths_b.video_path), "SQLite stored a legacy video path")
        _assert(entry.get("audio_path") == str(paths_b.audio_path), "SQLite stored a legacy audio path")
        _assert(entry.get("thumb_path") == str(paths_b.thumb_path), "SQLite stored a legacy thumbnail path")


def _test_collision_rejection_does_not_mark_state_downloaded() -> None:
    with TemporaryDirectory(prefix="bug01_collision_state_") as temp_dir:
        root = Path(temp_dir)
        db_path = root / "state" / "download_state.sqlite3"
        base = root / "output"
        base.mkdir()
        options = _options(base, "shared-channel", "Channel", MODE_VIDEO_THUMB)
        paths = build_output_paths(base, options.channel_name, SHARED_STEM, options.channel_id)
        reserve_output_paths(
            paths.channel_dir,
            options.channel_id,
            PROCESS_A_VIDEO_ID,
            {PART_VIDEO: paths.video_path, PART_THUMB: paths.thumb_path},
        )
        logs: list[str] = []
        rejected_video = _video(PROCESS_B_VIDEO_ID)
        with _patched_state_db(db_path), _patched_download_environment(), _transfers_must_not_run():
            downloader.download_items([rejected_video], options, logs.append, lambda _video: None)
            entry = state_store.get_video_entry(options.channel_id, PROCESS_B_VIDEO_ID)
        _assert(entry is None, "collision rejection created downloaded state for the rejected video")
        _assert(rejected_video.status == state_store.STATUS_ERROR, "collision rejection did not surface an error status")
        _assert(any("Output filename collision" in line for line in logs), "collision rejection log was unclear")


def _test_numbered_prefix_semantics_are_unchanged() -> None:
    with TemporaryDirectory(prefix="bug01_numbering_") as temp_dir:
        options = _options(Path(temp_dir), "channel", "Channel", MODE_VIDEO_THUMB)
        options.file_start_number = 7
        first_number, first_stem, _first_paths = downloader._prepare_numbered_output_for_video(
            _video("first"), options, 0
        )
        second_number, second_stem, _second_paths = downloader._prepare_numbered_output_for_video(
            _video("second"), options, 1
        )
        _assert((first_number, second_number) == (7, 8), "file_start_number + selected_index changed")
        _assert(first_stem.startswith("007 ") and second_stem.startswith("008 "), "numbered prefixes changed")


def _promotion_worker(video_id, source_path, final_path, ready, start, results) -> None:
    try:
        ready.set()
        if not start.wait(timeout=10):
            raise RuntimeError("promotion barrier timed out")
        final_path = Path(final_path)
        reservation = reserve_output_paths(
            final_path.parent.parent,
            "shared-channel",
            video_id,
            {PART_VIDEO: final_path},
        )
        downloader._atomic_promote_with_retry(
            Path(source_path),
            final_path,
            replace_existing=True,
            ownership_claim=reservation.claim_for_path(final_path),
        )
    except Exception as exc:
        results.put((video_id, "rejected", type(exc).__name__))
    else:
        results.put((video_id, "success", ""))


def _channel_resolution_worker(base_folder, ready, start, results) -> None:
    ready.set()
    if not start.wait(timeout=10):
        results.put("timeout")
        return
    resolved = channel_dir_for(base_folder, "Shared Channel", "stable-channel-id")
    results.put(str(resolved))


def _abandon_mutex_worker(resource, ready) -> None:
    with named_mutex(resource, timeout_seconds=5):
        ready.set()
        os._exit(0)


def _video(video_id: str):
    return SimpleNamespace(
        video_id=video_id,
        title="Example",
        sanitized_filename_base=SHARED_STEM,
        display_order=1,
        thumbnail_url="",
        status=state_store.STATUS_NOT_DOWNLOADED,
    )


def _options(base_folder: Path, channel_id: str, channel_name: str, download_mode: str) -> DownloadOptions:
    return DownloadOptions(
        base_folder=str(base_folder),
        channel_id=channel_id,
        channel_name=channel_name,
        download_mode=download_mode,
        file_start_number=1,
    )


@contextmanager
def _patched_state_db(db_path: Path):
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
def _patched_download_environment():
    old_validate = downloader.validate_download_environment
    old_summary = downloader._log_runtime_tool_summary
    try:
        downloader.validate_download_environment = lambda options: setattr(
            options,
            "file_start_number",
            downloader.validate_file_start_number(options.file_start_number),
        )
        downloader._log_runtime_tool_summary = lambda _log: None
        yield
    finally:
        downloader.validate_download_environment = old_validate
        downloader._log_runtime_tool_summary = old_summary


@contextmanager
def _transfers_must_not_run():
    old_video = downloader._download_video
    old_audio = downloader._download_audio
    old_thumb = downloader._download_thumbnail
    old_extract = downloader._extract_mp3_from_video

    def unexpected(*_args, **_kwargs):
        raise AssertionError("media transfer ran unexpectedly")

    try:
        downloader._download_video = unexpected
        downloader._download_audio = unexpected
        downloader._download_thumbnail = unexpected
        downloader._extract_mp3_from_video = unexpected
        yield
    finally:
        downloader._download_video = old_video
        downloader._download_audio = old_audio
        downloader._download_thumbnail = old_thumb
        downloader._extract_mp3_from_video = old_extract


def _assert_raises(error_type, action, message: str) -> None:
    try:
        action()
    except error_type:
        return
    raise AssertionError(message)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
