import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.download_modes import MODE_AUDIO_THUMB, MODE_VIDEO_AUDIO_THUMB, MODE_VIDEO_THUMB, PART_AUDIO, PART_THUMB, PART_VIDEO
from core.downloader import DOWNLOAD_ENGINE_ARIA2_FAST, DownloadController, DownloadError, DownloadOptions, SkipCurrentVideo
from core.state_store import STATUS_DOWNLOADED, STATUS_ERROR


def main() -> int:
    _test_phase_order()
    _test_source_skip_stops_item_and_preserves_later_number()
    _test_thumbnail_skip_prevents_conversion()
    _test_defensive_phase_2_skip_guard()
    _test_source_failure_does_not_stop_later_jobs()
    _test_convert_failure_does_not_stop_later_jobs()
    _test_cancel_phase_1_stops_before_conversion()
    _test_cancel_phase_2_stops_remaining_conversions()
    _test_video_audio_thumb_extracts_audio_in_phase_2()
    _test_fast_audio_only_stays_sequential()
    print("fast two-phase batch smoke passed")
    return 0


def _test_phase_order() -> None:
    result = _run_two_phase(["A", "B", "C"])
    _assert(
        result.calls
        == [
            "download_source:A",
            "download_thumb:A",
            "download_source:B",
            "download_thumb:B",
            "download_source:C",
            "download_thumb:C",
            "convert:A",
            "convert:B",
            "convert:C",
        ],
        f"wrong phase order: {result.calls}",
    )
    first_convert = result.calls.index("convert:A")
    _assert(all(not call.startswith("convert:") for call in result.calls[:first_convert]), "conversion started during phase 1")
    _assert(result.counts == (3, 0, 0, False), f"unexpected counts: {result.counts}")
    thumb_updates = [item for item in result.state_updates if item[1] == PART_THUMB and item[2] == STATUS_DOWNLOADED]
    video_updates = [item for item in result.state_updates if item[1] == PART_VIDEO and item[2] == STATUS_DOWNLOADED]
    _assert(len(thumb_updates) == 3 and len(video_updates) == 3, "state updates did not mark all final parts")
    _assert(result.state_updates.index(video_updates[0]) > result.state_updates.index(thumb_updates[-1]), "video was marked downloaded before phase 2")


def _test_source_failure_does_not_stop_later_jobs() -> None:
    result = _run_two_phase(["A", "B", "C"], source_failures={"B"})
    _assert(
        result.calls
        == [
            "download_source:A",
            "download_thumb:A",
            "download_source:B",
            "download_thumb:B",
            "download_source:C",
            "download_thumb:C",
            "convert:A",
            "convert:C",
        ],
        f"wrong source-failure order: {result.calls}",
    )
    _assert(result.counts == (2, 1, 0, False), f"source failure counts wrong: {result.counts}")
    _assert(("B", PART_VIDEO, STATUS_ERROR) in result.state_updates, "source failure did not mark B video error")
    _assert("convert:B" not in result.calls, "source failure was queued for conversion")


def _test_convert_failure_does_not_stop_later_jobs() -> None:
    result = _run_two_phase(["A", "B", "C"], convert_failures={"B"})
    _assert(result.calls[-3:] == ["convert:A", "convert:B", "convert:C"], f"wrong conversion order: {result.calls}")
    _assert(result.counts == (2, 1, 0, False), f"convert failure counts wrong: {result.counts}")
    _assert(("B", PART_VIDEO, STATUS_ERROR) in result.state_updates, "conversion failure did not mark B video error")


def _test_source_skip_stops_item_and_preserves_later_number() -> None:
    result = _run_two_phase(["A", "B"], source_skips={"A"}, title_prefix="Title ")
    _assert(
        result.calls == ["download_source:A", "download_source:B", "download_thumb:B", "convert:B"],
        f"source skip did not stop A and continue B: {result.calls}",
    )
    _assert(result.counts == (1, 0, 1, False), f"source skip counts wrong: {result.counts}")
    _assert("download_thumb:A" not in result.calls, "source skip still downloaded thumbnail A")
    _assert("convert:A" not in result.calls, "source skip still converted A")
    _assert("A" in result.reconciled, "source skip did not reconcile existing state")
    _assert(not any(update[0] == "A" and update[1] in (PART_VIDEO, PART_THUMB) for update in result.state_updates), "source skip wrote part state")
    _assert("102 Title B" in {path.stem for path in result.final_paths}, "B did not retain its original assigned number")


def _test_thumbnail_skip_prevents_conversion() -> None:
    result = _run_two_phase(["A"], thumbnail_skips={"A"})
    _assert(result.calls == ["download_source:A", "download_thumb:A"], f"thumbnail skip order wrong: {result.calls}")
    _assert(result.counts == (0, 0, 1, False), f"thumbnail skip counts wrong: {result.counts}")
    _assert("convert:A" not in result.calls, "thumbnail skip still converted A")
    _assert("A" in result.source_created, "thumbnail skip test did not stage source A")
    _assert("A" not in result.converted, "thumbnail skip promoted/converted source A")
    _assert("A" in result.reconciled, "thumbnail skip did not reconcile existing state")
    _assert(not any(update[0] == "A" and update[1] in (PART_VIDEO, PART_THUMB) for update in result.state_updates), "thumbnail skip wrote downloaded/error state")
    _assert(not any(path.name == "101 A.mp4" for path in result.final_paths), "thumbnail skip created final video")


def _test_defensive_phase_2_skip_guard() -> None:
    result = _run_two_phase(["A"], mark_queued_skipped_before_phase2=True)
    _assert("download_source:A" in result.calls and "download_thumb:A" in result.calls, "defensive test did not queue phase-1 work")
    _assert("convert:A" not in result.calls, "phase-2 guard converted a skipped job")
    _assert(result.converted == set(), "phase-2 guard promoted a skipped job")
    _assert(result.counts == (0, 0, 0, False), f"phase-2 guard changed counters: {result.counts}")


def _test_cancel_phase_1_stops_before_conversion() -> None:
    controller = DownloadController()
    result = _run_two_phase(["A", "B"], controller=controller, cancel_source="A")
    _assert(result.calls == ["download_source:A"], f"phase 1 cancellation continued work: {result.calls}")
    _assert(result.counts == (0, 0, 0, True), f"phase 1 cancellation counts wrong: {result.counts}")


def _test_cancel_phase_2_stops_remaining_conversions() -> None:
    controller = DownloadController()
    result = _run_two_phase(["A", "B", "C"], controller=controller, cancel_convert="A")
    _assert(result.calls[-1] == "convert:A", f"phase 2 did not stop after first conversion: {result.calls}")
    _assert("convert:B" not in result.calls and "convert:C" not in result.calls, "later conversions ran after cancel")
    _assert(result.counts == (1, 0, 0, True), f"phase 2 cancellation counts wrong: {result.counts}")


def _test_video_audio_thumb_extracts_audio_in_phase_2() -> None:
    result = _run_two_phase(["A"], download_mode=MODE_VIDEO_AUDIO_THUMB)
    _assert(
        result.calls == ["download_source:A", "download_thumb:A", "convert:A", "extract_audio:A"],
        f"video+audio+thumb order wrong: {result.calls}",
    )
    stems = {path.stem for path in result.final_paths}
    _assert(stems == {"101 A"}, f"video/audio/thumb did not share one numbered stem: {stems}")
    _assert(("A", PART_AUDIO, STATUS_DOWNLOADED) in result.state_updates, "audio was not marked downloaded")


def _test_fast_audio_only_stays_sequential() -> None:
    with TemporaryDirectory(prefix="fast_audio_only_") as temp_dir:
        options = DownloadOptions(
            base_folder=temp_dir,
            channel_id="channel",
            channel_name="Channel",
            download_engine=DOWNLOAD_ENGINE_ARIA2_FAST,
            download_mode=MODE_AUDIO_THUMB,
            file_start_number=9,
        )
        videos = [_video("A")]
        calls: list[str] = []
        captured_paths: list[Path] = []

        old_validate = downloader.validate_download_environment
        old_summary = downloader._call_runtime_tool_summary
        old_runtime = downloader._prepare_media_downloader_runtime
        old_two_phase = downloader._download_fast_video_batch_two_phase
        old_missing = downloader._missing_parts_for_current_paths
        old_audio = downloader._download_audio
        old_thumb = downloader._download_thumbnail
        old_update = downloader.update_video_part_state
        old_get_entry = downloader.get_video_entry
        old_effective = downloader.get_effective_status
        old_reconcile = downloader._reconcile_current_item
        try:
            downloader.validate_download_environment = lambda opts: setattr(
                opts, "file_start_number", downloader.validate_file_start_number(opts.file_start_number)
            )
            downloader._call_runtime_tool_summary = lambda *_args, **_kwargs: None
            downloader._prepare_media_downloader_runtime = lambda *_args, **_kwargs: _aria2_validation()
            downloader._download_fast_video_batch_two_phase = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("Fast audio-only entered video two-phase path")
            )
            downloader._missing_parts_for_current_paths = lambda *_args, **_kwargs: (PART_AUDIO, PART_THUMB)

            def fake_audio(video_id, stem, temp_dir, final_path, *_args, **_kwargs):
                calls.append(f"audio:{video_id}")
                captured_paths.append(Path(final_path))
                Path(final_path).parent.mkdir(parents=True, exist_ok=True)
                Path(final_path).write_bytes(b"mp3")

            def fake_thumb(video, stem, temp_dir, final_path, *_args, **_kwargs):
                calls.append(f"thumb:{video.video_id}")
                captured_paths.append(Path(final_path))
                Path(final_path).parent.mkdir(parents=True, exist_ok=True)
                Path(final_path).write_bytes(b"jpg")

            downloader._download_audio = fake_audio
            downloader._download_thumbnail = fake_thumb
            downloader.update_video_part_state = lambda *_args, **_kwargs: None
            downloader.get_video_entry = lambda *_args, **_kwargs: {}
            downloader.get_effective_status = lambda *_args, **_kwargs: STATUS_DOWNLOADED
            downloader._reconcile_current_item = lambda *_args, **_kwargs: STATUS_DOWNLOADED
            downloader.download_items(videos, options, lambda _message: None, lambda _video: None)
        finally:
            downloader.validate_download_environment = old_validate
            downloader._call_runtime_tool_summary = old_summary
            downloader._prepare_media_downloader_runtime = old_runtime
            downloader._download_fast_video_batch_two_phase = old_two_phase
            downloader._missing_parts_for_current_paths = old_missing
            downloader._download_audio = old_audio
            downloader._download_thumbnail = old_thumb
            downloader.update_video_part_state = old_update
            downloader.get_video_entry = old_get_entry
            downloader.get_effective_status = old_effective
            downloader._reconcile_current_item = old_reconcile

    _assert(calls == ["audio:A", "thumb:A"], f"fast audio-only was not sequential: {calls}")
    _assert({path.stem for path in captured_paths} == {"009 A"}, "fast audio-only did not use numbered stem")


def _run_two_phase(
    video_ids: list[str],
    *,
    source_failures: set[str] | None = None,
    source_skips: set[str] | None = None,
    thumbnail_skips: set[str] | None = None,
    convert_failures: set[str] | None = None,
    cancel_source: str = "",
    cancel_convert: str = "",
    controller: DownloadController | None = None,
    download_mode: str = MODE_VIDEO_THUMB,
    title_prefix: str = "",
    mark_queued_skipped_before_phase2: bool = False,
):
    source_failures = source_failures or set()
    source_skips = source_skips or set()
    thumbnail_skips = thumbnail_skips or set()
    convert_failures = convert_failures or set()
    controller = controller or DownloadController()
    calls: list[str] = []
    state_updates: list[tuple[str, str, str]] = []
    final_paths: list[Path] = []
    source_created: set[str] = set()
    converted: set[str] = set()
    reconciled: list[str] = []
    created_jobs: list[downloader._FastBatchVideoJob] = []

    with TemporaryDirectory(prefix="fast_two_phase_") as temp_dir:
        options = DownloadOptions(
            base_folder=temp_dir,
            channel_id="channel",
            channel_name="Channel",
            download_engine=DOWNLOAD_ENGINE_ARIA2_FAST,
            download_mode=download_mode,
            file_start_number=101,
        )
        videos = [_video(video_id, title=f"{title_prefix}{video_id}") for video_id in video_ids]

        old_missing = downloader._missing_parts_for_current_paths
        old_source = downloader._download_fast_video_source
        old_thumb = downloader._download_thumbnail
        old_convert = downloader._convert_and_promote_fast_video
        old_extract = downloader._extract_mp3_from_video
        old_update = downloader.update_video_part_state
        old_get_entry = downloader.get_video_entry
        old_effective = downloader.get_effective_status
        old_reconcile = downloader._reconcile_current_item
        old_job_class = downloader._FastBatchVideoJob
        old_raise_cancelled = downloader._raise_if_cancelled
        try:
            downloader._missing_parts_for_current_paths = lambda *_args, **_kwargs: tuple(downloader.required_parts(options.download_mode))

            def fake_source(video_id, staging_dir, *_args, **_kwargs):
                calls.append(f"download_source:{video_id}")
                if video_id == cancel_source:
                    controller.request_cancel()
                if video_id in source_skips:
                    raise SkipCurrentVideo()
                if video_id in source_failures:
                    raise DownloadError(f"source failed: {video_id}")
                source = Path(staging_dir) / f"{video_id}.mp4"
                source.write_bytes(b"source")
                source_created.add(video_id)
                return source

            def fake_thumb(video, stem, temp_dir, final_path, *_args, **_kwargs):
                calls.append(f"download_thumb:{video.video_id}")
                if video.video_id in thumbnail_skips:
                    raise SkipCurrentVideo()
                Path(final_path).parent.mkdir(parents=True, exist_ok=True)
                Path(final_path).write_bytes(b"jpg")
                final_paths.append(Path(final_path))

            def fake_convert(source_mp4_path, staging_dir, final_path, *_args, **_kwargs):
                video_id = Path(source_mp4_path).stem
                calls.append(f"convert:{video_id}")
                if video_id == cancel_convert:
                    controller.request_cancel()
                if video_id in convert_failures:
                    raise DownloadError(f"convert failed: {video_id}")
                Path(final_path).parent.mkdir(parents=True, exist_ok=True)
                Path(final_path).write_bytes(b"mp4")
                converted.add(video_id)
                final_paths.append(Path(final_path))

            def fake_extract(source_video_path, temp_dir, final_audio_path, *_args, **_kwargs):
                video_id = Path(source_video_path).stem.split(" ", 1)[-1]
                calls.append(f"extract_audio:{video_id}")
                Path(final_audio_path).parent.mkdir(parents=True, exist_ok=True)
                Path(final_audio_path).write_bytes(b"mp3")
                final_paths.append(Path(final_audio_path))

            def fake_update(_channel_id, _channel_name, _base_folder, video, _paths, part, status, *_args, **_kwargs):
                state_updates.append((video.video_id, part, status))

            def fake_reconcile(_options, video, _paths, *_args, **_kwargs):
                reconciled.append(video.video_id)
                if video.video_id in source_failures or video.video_id in convert_failures:
                    return STATUS_ERROR
                return STATUS_DOWNLOADED

            def fake_job(*args, **kwargs):
                job = old_job_class(*args, **kwargs)
                created_jobs.append(job)
                return job

            def mark_jobs_skipped_before_phase2(cancel_controller=None):
                if mark_queued_skipped_before_phase2:
                    for job in created_jobs:
                        job.skipped = True
                old_raise_cancelled(cancel_controller)

            downloader._download_fast_video_source = fake_source
            downloader._download_thumbnail = fake_thumb
            downloader._convert_and_promote_fast_video = fake_convert
            downloader._extract_mp3_from_video = fake_extract
            downloader.update_video_part_state = fake_update
            downloader.get_video_entry = lambda *_args, **_kwargs: {}
            downloader.get_effective_status = lambda *_args, **_kwargs: STATUS_DOWNLOADED
            downloader._reconcile_current_item = fake_reconcile
            downloader._FastBatchVideoJob = fake_job
            downloader._raise_if_cancelled = mark_jobs_skipped_before_phase2
            counts = downloader._download_fast_video_batch_two_phase(
                videos,
                options,
                lambda _message: None,
                lambda _video: None,
                controller,
                None,
                _aria2_validation(),
            )
        finally:
            downloader._missing_parts_for_current_paths = old_missing
            downloader._download_fast_video_source = old_source
            downloader._download_thumbnail = old_thumb
            downloader._convert_and_promote_fast_video = old_convert
            downloader._extract_mp3_from_video = old_extract
            downloader.update_video_part_state = old_update
            downloader.get_video_entry = old_get_entry
            downloader.get_effective_status = old_effective
            downloader._reconcile_current_item = old_reconcile
            downloader._FastBatchVideoJob = old_job_class
            downloader._raise_if_cancelled = old_raise_cancelled

    return SimpleNamespace(
        calls=calls,
        state_updates=state_updates,
        counts=counts,
        final_paths=final_paths,
        source_created=source_created,
        converted=converted,
        reconciled=reconciled,
    )


def _aria2_validation() -> downloader._Aria2RuntimeValidation:
    return downloader._Aria2RuntimeValidation(True, True, Path("aria2c.exe"))


def _video(video_id: str, *, title: str | None = None):
    return SimpleNamespace(
        video_id=video_id,
        title=video_id if title is None else title,
        sanitized_filename_base=video_id if title is None else title,
        thumbnail_url="",
        status="",
        display_order=1,
    )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
