import json
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.download_process import DownloadController
from core.downloader import (
    DOWNLOAD_ENGINE_ARIA2_FAST,
    DOWNLOAD_ENGINE_STABLE,
    DownloadCancelled,
    DownloadError,
    DownloadOptions,
)
from core.progress_status import TRANSFER_SOURCE_YTDLP, STAGE_MERGING


VIDEO_ID = "video-id"
VIDEO_FORMAT_ID = "137"
AUDIO_FORMAT_ID = "140"
COMBINED_FORMAT_ID = "synthetic-combined-720"


def main() -> int:
    _test_fast_video_uses_one_snapshot_and_split_transports()
    _test_fast_video_preserves_combined_fallback()
    _test_hybrid_cleanup_and_failure_boundaries()
    _test_cancellation_between_video_and_audio()
    _test_stable_and_separate_mp3_paths_remain_unchanged()
    _test_hybrid_progress_is_stage_correct_and_non_decreasing()
    _test_combined_fallback_selection_is_preserved()
    _test_invalid_hybrid_metadata_is_rejected()
    print("fast hybrid video smoke passed")
    return 0


def _test_fast_video_uses_one_snapshot_and_split_transports() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        options = DownloadOptions(
            base_folder=str(root / "downloads"),
            channel_id="channel",
            channel_name="Channel",
            download_engine=DOWNLOAD_ENGINE_ARIA2_FAST,
            speed_limit="2M",
            file_start_number=1,
        )
        commands: list[list[str]] = []
        ffmpeg_commands: list[list[str]] = []
        promotions: list[tuple[Path, Path]] = []
        final_path = root / "final.mp4"

        def fake_retry(command, current_options, log, cancel_controller=None, cookie_retry_state=None):
            captured = list(command)
            commands.append(captured)
            output_template = _option_value(captured, "-o")
            if "--write-info-json" in captured:
                info_path = Path(output_template.replace("%(ext)s", "info.json"))
                info_path.parent.mkdir(parents=True, exist_ok=True)
                info_path.write_text(json.dumps(_selected_info()), encoding="utf-8")
                return
            format_id = _option_value(captured, "-f")
            suffix = (
                ".m4a"
                if "--load-info-json" in captured and format_id == AUDIO_FORMAT_ID
                else ".mp4"
            )
            output_path = Path(output_template.replace("%(ext)s", suffix.removeprefix(".")))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(format_id.encode("ascii"))

        def fake_ffmpeg(command, *, operation, cancel_controller=None, progress_duration_seconds=None):
            captured = list(command)
            ffmpeg_commands.append(captured)
            Path(captured[-1]).write_bytes(b"merged")
            return ""

        def fake_promote(source, final, log, replace_existing=False, cancel_controller=None):
            promotions.append((Path(source), Path(final)))
            Path(final).parent.mkdir(parents=True, exist_ok=True)
            Path(final).write_bytes(Path(source).read_bytes())

        with (
            _patched_runtime(paths),
            _patched_attr(downloader, "_run_ytdlp_with_retries", fake_retry),
            _patched_attr(downloader, "_run_ffmpeg_command", fake_ffmpeg),
            _patched_attr(downloader, "_validate_premiere_safe_mp4_for_download", lambda *args, **kwargs: None),
            _patched_attr(downloader, "_atomic_promote_with_retry", fake_promote),
        ):
            downloader._download_video(
                VIDEO_ID,
                "001 Title",
                root,
                final_path,
                options,
                lambda _message: None,
                aria2_validation=downloader._Aria2RuntimeValidation(True, True, paths.aria2),
            )

        extraction_commands = [command for command in commands if "--write-info-json" in command]
        media_commands = [command for command in commands if "--load-info-json" in command]
        _assert(len(extraction_commands) == 1, f"Expected one metadata extraction, got {commands}")
        _assert(len(media_commands) == 2, f"Expected two saved-media transfers, got {commands}")

        extraction = extraction_commands[0]
        video = next(command for command in media_commands if _option_value(command, "-f") == VIDEO_FORMAT_ID)
        audio = next(command for command in media_commands if _option_value(command, "-f") == AUDIO_FORMAT_ID)
        _assert("--skip-download" in extraction, "Metadata extraction did not skip media")
        _assert("--downloader" not in extraction, "Metadata extraction used aria2")
        _assert(_option_value(video, "--load-info-json") == _option_value(audio, "--load-info-json"), "Media legs used different snapshots")
        _assert("--downloader" not in video, "Video leg retained an external downloader")
        _assert("--downloader-args" not in video, "Video leg retained external downloader args")
        _assert(_option_value(video, "-N") == "1", "Video leg did not use native -N 1")
        _assert("--downloader" not in audio, "Companion audio used aria2")
        _assert("--downloader-args" not in audio, "Companion audio retained external downloader args")
        _assert(_option_value(audio, "-N") == "1", "Companion audio did not use native -N 1")
        _assert(not any(value.startswith("https://") for value in video), "Video leg retained a normal extractor URL")
        _assert(not any(value.startswith("https://") for value in audio), "Audio leg retained a normal extractor URL")
        _assert(_option_value(video, "--limit-rate") == "2M", "Video speed limit was lost")
        _assert(_option_value(audio, "--limit-rate") == "2M", "Audio speed limit was lost")
        _assert(len(ffmpeg_commands) == 1, "Expected one final merge")
        _assert("-c" in ffmpeg_commands[0] and _option_value(ffmpeg_commands[0], "-c") == "copy", "Merge was not stream copy")
        _assert(len(promotions) == 1, f"Intermediate streams reached promotion: {promotions}")
        _assert(promotions[0][0].name == "merged.mp4", f"Non-merged source was promoted: {promotions}")
        _assert(final_path.read_bytes() == b"merged", "Merged output was not promoted")
        _assert(not list(root.rglob("*.info.json")), "Temporary info JSON survived success")
        _assert(not list(root.glob(".s9h-stage-*")), "Hybrid staging survived success")


def _test_fast_video_preserves_combined_fallback() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        options = DownloadOptions(
            base_folder=str(root / "downloads"),
            channel_id="channel",
            channel_name="Channel",
            download_engine=DOWNLOAD_ENGINE_ARIA2_FAST,
            speed_limit="2M",
            file_start_number=1,
        )
        commands: list[list[str]] = []
        ffmpeg_commands: list[list[str]] = []
        validations: list[Path] = []
        promotions: list[tuple[Path, Path]] = []
        events = []
        final_path = root / "final.mp4"

        def fake_retry(command, current_options, log, cancel_controller=None, cookie_retry_state=None):
            captured = list(command)
            commands.append(captured)
            output_template = _option_value(captured, "-o")
            if "--write-info-json" in captured:
                info_path = Path(output_template.replace("%(ext)s", "info.json"))
                info_path.parent.mkdir(parents=True, exist_ok=True)
                info_path.write_text(json.dumps(_combined_info()), encoding="utf-8")
                return
            output_path = Path(output_template.replace("%(ext)s", "mp4"))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"combined")
            downloader._start_progress_attempt(TRANSFER_SOURCE_YTDLP)
            downloader._emit_ytdlp_progress_from_line(
                "[download]   0.0% of 10.00MiB at 1.00MiB/s ETA 00:10",
                TRANSFER_SOURCE_YTDLP,
            )
            downloader._emit_ytdlp_progress_from_line(
                "[download] 100.0% of 10.00MiB at 1.00MiB/s ETA 00:00",
                TRANSFER_SOURCE_YTDLP,
            )

        def fake_validate(source, log, strict, cancel_controller=None):
            validations.append(Path(source))

        def fake_ffmpeg(command, *, operation, cancel_controller=None, progress_duration_seconds=None):
            ffmpeg_commands.append(list(command))
            return ""

        def fake_promote(source, final, log, replace_existing=False, cancel_controller=None):
            promotions.append((Path(source), Path(final)))
            Path(final).write_bytes(Path(source).read_bytes())

        video = SimpleNamespace(video_id=VIDEO_ID, title="Title", sanitized_filename_base="001 Title")
        previous = downloader._set_progress_context(events.append, video, 1, 1, "Video")
        try:
            with (
                _patched_runtime(paths),
                _patched_attr(downloader, "_run_ytdlp_with_retries", fake_retry),
                _patched_attr(downloader, "_run_ffmpeg_command", fake_ffmpeg),
                _patched_attr(downloader, "_validate_premiere_safe_mp4_for_download", fake_validate),
                _patched_attr(downloader, "_atomic_promote_with_retry", fake_promote),
            ):
                downloader._download_video(
                    VIDEO_ID,
                    "001 Title",
                    root,
                    final_path,
                    options,
                    lambda _message: None,
                    aria2_validation=downloader._Aria2RuntimeValidation(True, True, paths.aria2),
                )
        finally:
            downloader._restore_progress_context(previous)

        extraction_commands = [command for command in commands if "--write-info-json" in command]
        media_commands = [command for command in commands if "--load-info-json" in command]
        _assert(len(extraction_commands) == 1, f"Combined fallback extracted metadata more than once: {commands}")
        _assert(len(media_commands) == 1, f"Combined fallback started extra media transfers: {commands}")
        media = media_commands[0]
        _assert(_option_value(media, "-f") == COMBINED_FORMAT_ID, f"Combined exact format ID changed: {media}")
        _assert("--downloader" not in media, "Combined fallback retained an external downloader")
        _assert("--downloader-args" not in media, "Combined fallback retained external downloader args")
        _assert(_option_value(media, "-N") == "1", "Combined fallback did not use native -N 1")
        _assert(_option_value(media, "--limit-rate") == "2M", "Combined speed limit was lost")
        _assert(not any(value.startswith("https://") for value in media), "Combined fallback retained a normal extractor URL")
        _assert(_option_value(media, "--load-info-json") == _option_value(extraction_commands[0], "-o").replace("%(ext)s", "info.json"), "Combined media did not reuse its extraction snapshot")
        _assert(not ffmpeg_commands, f"Combined fallback started an extra merge: {ffmpeg_commands}")
        _assert([path.name for path in validations] == ["video.mp4"], f"Combined output skipped validation: {validations}")
        _assert(len(promotions) == 1 and promotions[0][0].name == "video.mp4", f"Combined output promotion changed: {promotions}")
        _assert(final_path.read_bytes() == b"combined", "Combined output was not promoted")
        numeric = [float(event.percent.removesuffix("%")) for event in events if event.percent]
        _assert(numeric == sorted(numeric), f"Combined progress moved backwards: {numeric}")
        _assert(any(event.phase == "Video" and event.message == "Downloading video" for event in events), "Combined video stage was not emitted")
        _assert(not any(event.phase == "Companion audio" for event in events), "Combined progress emitted a companion-audio stage")
        _assert(not any(event.phase == "Merging" for event in events), "Combined progress emitted a merge stage")
        _assert(not list(root.rglob("*.info.json")), "Combined info JSON survived success")
        _assert(not list(root.glob(".s9h-stage-*")), "Combined staging survived success")


def _test_hybrid_cleanup_and_failure_boundaries() -> None:
    success = _run_fast_case()
    _assert(success.error is None, f"Hybrid success failed: {success.error}")
    _assert(success.final_exists, "Hybrid success did not create final MP4")
    _assert(success.promotions == ["merged.mp4"], f"Intermediate stream was promoted: {success.promotions}")
    _assert(success.info_json_remaining == 0, "Info JSON survived success")
    _assert(success.hybrid_staging_remaining == 0, "Hybrid staging survived success")

    metadata_failure = _run_fast_case(fail_stage="metadata")
    _assert(isinstance(metadata_failure.error, DownloadError), "Metadata failure did not propagate")
    _assert(metadata_failure.transport_stages == [], "Metadata failure started media transfer")
    _assert(not metadata_failure.promotions, "Metadata failure promoted output")
    _assert(metadata_failure.info_json_remaining == 0, "Info JSON survived metadata failure")
    _assert(metadata_failure.hybrid_staging_remaining == 0, "Hybrid staging survived metadata failure")

    video_failure = _run_fast_case(fail_stage="video")
    _assert(isinstance(video_failure.error, DownloadError), "Video failure did not propagate")
    _assert(video_failure.transport_stages == ["video"], f"Video failure started a later stage: {video_failure.transport_stages}")
    _assert(not video_failure.promotions, "Video failure promoted output")
    _assert(not video_failure.final_exists, "Video failure created final MP4")
    _assert(video_failure.info_json_remaining == 0, "Info JSON survived video failure")

    audio_failure = _run_fast_case(fail_stage="audio")
    _assert(isinstance(audio_failure.error, DownloadError), "Audio failure did not propagate")
    _assert(audio_failure.transport_stages == ["video", "audio"], f"Audio failure stages changed: {audio_failure.transport_stages}")
    _assert(not audio_failure.ffmpeg_commands, "Audio failure started final merge")
    _assert(not audio_failure.promotions, "Audio failure promoted output")
    _assert(not audio_failure.final_exists, "Audio failure created final MP4")
    _assert(audio_failure.info_json_remaining == 0, "Info JSON survived audio failure")
    _assert(audio_failure.hybrid_staging_remaining == 0, "Hybrid staging survived audio failure")


def _test_cancellation_between_video_and_audio() -> None:
    result = _run_fast_case(cancel_after_video=True)
    _assert(isinstance(result.error, DownloadCancelled), f"Cancellation did not propagate: {result.error}")
    _assert(result.transport_stages == ["video"], f"Cancellation started companion audio: {result.transport_stages}")
    _assert(not result.ffmpeg_commands, "Cancellation started merge")
    _assert(not result.promotions, "Cancellation promoted output")
    _assert(not result.final_exists, "Cancellation created final MP4")
    _assert(result.info_json_remaining == 0, "Info JSON survived cancellation")
    _assert(result.hybrid_staging_remaining == 0, "Hybrid staging survived cancellation")


def _test_stable_and_separate_mp3_paths_remain_unchanged() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        stable_options = DownloadOptions(
            base_folder=str(root / "downloads"),
            channel_id="channel",
            channel_name="Channel",
            download_engine=DOWNLOAD_ENGINE_STABLE,
            speed_limit="3M",
            file_start_number=1,
        )
        fast_options = DownloadOptions(
            base_folder=str(root / "downloads"),
            channel_id="channel",
            channel_name="Channel",
            download_engine=DOWNLOAD_ENGINE_ARIA2_FAST,
            speed_limit="3M",
            file_start_number=1,
        )
        with _patched_runtime(paths):
            stable_video = downloader._build_stable_video_ytdlp_command(VIDEO_ID, root, stable_options)
            separate_mp3 = downloader._build_fast_audio_ytdlp_command(
                VIDEO_ID,
                root,
                fast_options,
                downloader._Aria2RuntimeValidation(True, True, paths.aria2),
            )

    _assert("--load-info-json" not in stable_video, "Stable video was routed through hybrid metadata")
    _assert("--downloader" not in stable_video, "Stable video started using aria2")
    _assert(_option_value(stable_video, "-f") == downloader.PREMIERE_SAFE_VIDEO_FORMAT, "Stable selector changed")
    _assert("-x" in separate_mp3, "Separate MP3 extraction flag changed")
    _assert(_option_value(separate_mp3, "--audio-format") == "mp3", "Separate MP3 format changed")
    _assert(_option_value(separate_mp3, "--audio-quality") == "0", "Separate MP3 quality changed")
    _assert(_option_value(separate_mp3, "--downloader") == str(paths.aria2), "Separate Fast MP3 downloader changed")
    _assert("--load-info-json" not in separate_mp3, "Separate MP3 was confused with companion audio")


def _test_hybrid_progress_is_stage_correct_and_non_decreasing() -> None:
    events = []
    video = SimpleNamespace(video_id=VIDEO_ID, title="Title", sanitized_filename_base="001 Title")
    previous = downloader._set_progress_context(events.append, video, 1, 1, "Video")
    try:
        downloader._set_current_progress_stage(
            "Video",
            percent_start=0.0,
            percent_end=90.0,
            start_message="Downloading video",
        )
        downloader._start_progress_attempt(TRANSFER_SOURCE_YTDLP)
        downloader._emit_ytdlp_progress_from_line(
            "[download] 100.0% of 100.00MiB at 8.00MiB/s ETA 00:00",
            TRANSFER_SOURCE_YTDLP,
        )
        downloader._set_current_progress_stage(
            "Companion audio",
            percent_start=90.0,
            percent_end=100.0,
            start_message="Downloading companion audio",
        )
        downloader._start_progress_attempt(TRANSFER_SOURCE_YTDLP)
        downloader._emit_ytdlp_progress_from_line(
            "[download]   0.0% of 10.00MiB at 1.00MiB/s ETA 00:10",
            TRANSFER_SOURCE_YTDLP,
        )
        downloader._emit_ytdlp_progress_from_line(
            "[download] 100.0% of 10.00MiB at 1.00MiB/s ETA 00:00",
            TRANSFER_SOURCE_YTDLP,
        )
        downloader._set_current_progress_stage("Merging", percent_start=100.0, percent_end=100.0)
        downloader._emit_current_progress("Merging", message=STAGE_MERGING)
    finally:
        downloader._restore_progress_context(previous)

    numeric = [float(event.percent.removesuffix("%")) for event in events if event.percent]
    _assert(numeric == sorted(numeric), f"Hybrid progress moved backwards: {numeric}")
    _assert(any(event.phase == "Video" and event.message == "Downloading video" for event in events), "Video stage was not emitted")
    _assert(any(event.phase == "Companion audio" and event.message == "Downloading companion audio" for event in events), "Companion-audio stage was not emitted")
    _assert(any(event.phase == "Merging" and event.message == STAGE_MERGING for event in events), "Merge stage was not emitted")
    _assert(not any(event.kind in {"completed", "batch_complete"} for event in events), "Intermediate stage marked the item complete")
    _assert(downloader._part_from_progress_phase("Companion audio") == downloader.PART_VIDEO, "Companion-audio failure lost logical video scope")


def _test_combined_fallback_selection_is_preserved() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        combined_path = root / "combined.info.json"
        combined_path.write_text(
            json.dumps(_combined_info()),
            encoding="utf-8",
        )
        selection = downloader._load_hybrid_format_selection(combined_path)
        _assert(selection.kind == "combined", f"Combined fallback was not tagged correctly: {selection}")
        _assert(selection.video_format_id == COMBINED_FORMAT_ID, f"Combined fallback format changed: {selection}")
        _assert(selection.audio_format_id is None, f"Combined fallback invented companion audio: {selection}")
        _assert(downloader._hybrid_video_progress_end_percent(selection) == 100.0, "Combined progress did not span the full transfer")

        split_and_combined_path = root / "split-and-combined.info.json"
        split_and_combined = _selected_info()
        split_and_combined.update(_combined_info())
        split_and_combined["requested_formats"] = _selected_info()["requested_formats"]
        split_and_combined_path.write_text(json.dumps(split_and_combined), encoding="utf-8")
        split_selection = downloader._load_hybrid_format_selection(split_and_combined_path)
        _assert(split_selection.kind == "split", f"Valid split selection lost precedence: {split_selection}")


def _test_invalid_hybrid_metadata_is_rejected() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        lower_quality_path = root / "lower.info.json"
        lower_quality = _selected_info()
        lower_quality["requested_formats"][0]["vcodec"] = "vp9"
        lower_quality_path.write_text(json.dumps(lower_quality), encoding="utf-8")
        try:
            downloader._load_hybrid_format_selection(lower_quality_path)
        except DownloadError:
            pass
        else:
            raise AssertionError("Non-H.264 video metadata was accepted")

        unproven_height_path = root / "unproven-height.info.json"
        unproven_height = _selected_info()
        del unproven_height["requested_formats"][0]["height"]
        unproven_height_path.write_text(json.dumps(unproven_height), encoding="utf-8")
        try:
            downloader._load_hybrid_format_selection(unproven_height_path)
        except DownloadError:
            pass
        else:
            raise AssertionError("Video metadata without a proven <=1080p height was accepted")

        invalid_combined_variants = (
            ("container", {"ext": "webm"}),
            ("video-codec-vp9", {"vcodec": "vp9"}),
            ("video-codec-av1", {"vcodec": "av01.0.08M.08"}),
            ("audio-codec", {"acodec": "opus"}),
            ("height-missing", {"height": None}),
            ("height-too-high", {"height": 2160}),
            ("format-id-missing", {"format_id": ""}),
        )
        for name, changes in invalid_combined_variants:
            invalid_path = root / f"invalid-combined-{name}.info.json"
            invalid_payload = _combined_info()
            invalid_payload.update(changes)
            invalid_path.write_text(json.dumps(invalid_payload), encoding="utf-8")
            try:
                downloader._load_hybrid_format_selection(invalid_path)
            except DownloadError:
                pass
            else:
                raise AssertionError(f"Invalid combined fallback was accepted: {name}")

        invalid_split_with_combined_top_level_path = root / "invalid-split-with-combined-top-level.info.json"
        invalid_split_with_combined_top_level = _combined_info()
        invalid_split_with_combined_top_level["requested_formats"] = _selected_info()["requested_formats"]
        invalid_split_with_combined_top_level["requested_formats"][0]["vcodec"] = "vp9"
        invalid_split_with_combined_top_level_path.write_text(
            json.dumps(invalid_split_with_combined_top_level),
            encoding="utf-8",
        )
        try:
            downloader._load_hybrid_format_selection(invalid_split_with_combined_top_level_path)
        except DownloadError:
            pass
        else:
            raise AssertionError("Invalid split metadata was misclassified from aggregate top-level fields")


def _run_fast_case(*, fail_stage: str = "", cancel_after_video: bool = False):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        controller = DownloadController()
        options = DownloadOptions(
            base_folder=str(root / "downloads"),
            channel_id="channel",
            channel_name="Channel",
            download_engine=DOWNLOAD_ENGINE_ARIA2_FAST,
            speed_limit="2M",
            file_start_number=1,
        )
        transport_stages: list[str] = []
        ffmpeg_commands: list[list[str]] = []
        promotions: list[str] = []
        final_path = root / "final.mp4"

        def fake_retry(command, current_options, log, cancel_controller=None, cookie_retry_state=None):
            output_template = _option_value(command, "-o")
            if "--write-info-json" in command:
                if fail_stage == "metadata":
                    raise DownloadError("metadata failed")
                info_path = Path(output_template.replace("%(ext)s", "info.json"))
                info_path.parent.mkdir(parents=True, exist_ok=True)
                info_path.write_text(json.dumps(_selected_info()), encoding="utf-8")
                return

            format_id = _option_value(command, "-f")
            stage = "video" if format_id == VIDEO_FORMAT_ID else "audio"
            transport_stages.append(stage)
            if fail_stage == stage:
                raise DownloadError(f"{stage} failed")
            suffix = "mp4" if stage == "video" else "m4a"
            output_path = Path(output_template.replace("%(ext)s", suffix))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(stage.encode("ascii"))
            if stage == "video" and cancel_after_video:
                controller.request_cancel()

        def fake_ffmpeg(command, *, operation, cancel_controller=None, progress_duration_seconds=None):
            captured = list(command)
            ffmpeg_commands.append(captured)
            Path(captured[-1]).write_bytes(b"merged")
            return ""

        def fake_promote(source, final, log, replace_existing=False, cancel_controller=None):
            promotions.append(Path(source).name)
            Path(final).write_bytes(Path(source).read_bytes())

        error = None
        with (
            _patched_runtime(paths),
            _patched_attr(downloader, "_run_ytdlp_with_retries", fake_retry),
            _patched_attr(downloader, "_run_ffmpeg_command", fake_ffmpeg),
            _patched_attr(downloader, "_validate_premiere_safe_mp4_for_download", lambda *args, **kwargs: None),
            _patched_attr(downloader, "_atomic_promote_with_retry", fake_promote),
        ):
            try:
                downloader._download_video(
                    VIDEO_ID,
                    "001 Title",
                    root,
                    final_path,
                    options,
                    lambda _message: None,
                    controller,
                    aria2_validation=downloader._Aria2RuntimeValidation(True, True, paths.aria2),
                )
            except (DownloadCancelled, DownloadError) as exc:
                error = exc

        return SimpleNamespace(
            error=error,
            transport_stages=transport_stages,
            ffmpeg_commands=ffmpeg_commands,
            promotions=promotions,
            final_exists=final_path.exists(),
            info_json_remaining=len(list(root.rglob("*.info.json"))),
            hybrid_staging_remaining=len(list(root.glob(".s9h-stage-*"))),
        )


def _selected_info() -> dict:
    return {
        "id": VIDEO_ID,
        "duration": 120.0,
        "requested_formats": [
            {
                "format_id": VIDEO_FORMAT_ID,
                "ext": "mp4",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "height": 1080,
                "fps": 30,
                "filesize": 90_000_000,
            },
            {
                "format_id": AUDIO_FORMAT_ID,
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "filesize": 10_000_000,
            },
        ],
    }


def _combined_info() -> dict:
    return {
        "id": VIDEO_ID,
        "format_id": COMBINED_FORMAT_ID,
        "ext": "mp4",
        "vcodec": "avc1.64001F",
        "acodec": "mp4a.40.2",
        "height": 720,
        "filesize": 25_000_000,
        "duration": 120.0,
    }


def _runtime_paths(root: Path):
    bin_dir = root / "data" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ytdlp = bin_dir / "yt-dlp.exe"
    ffmpeg = bin_dir / "ffmpeg.exe"
    aria2 = bin_dir / "aria2c.exe"
    for path in (ytdlp, ffmpeg, aria2):
        path.write_text(path.stem, encoding="utf-8")
    return SimpleNamespace(root=root, bin=bin_dir, ytdlp=ytdlp, ffmpeg=ffmpeg, aria2=aria2)


@contextmanager
def _patched_runtime(paths):
    def fake_runtime_file(filename: str) -> Path:
        if filename == "yt-dlp.exe":
            return paths.ytdlp
        if filename == "ffmpeg.exe":
            return paths.ffmpeg
        if filename == "aria2c.exe":
            return paths.aria2
        if filename == "deno.exe":
            return paths.bin / "deno.exe"
        return paths.root / filename

    with _patched_attr(downloader, "runtime_file", fake_runtime_file):
        yield


@contextmanager
def _patched_attr(target, name: str, value):
    missing = object()
    old_value = getattr(target, name, missing)
    setattr(target, name, value)
    try:
        yield
    finally:
        if old_value is missing:
            delattr(target, name)
        else:
            setattr(target, name, old_value)


def _option_value(command: list[str], option: str) -> str:
    for index, value in enumerate(command):
        if value == option and index + 1 < len(command):
            return str(command[index + 1])
    return ""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
