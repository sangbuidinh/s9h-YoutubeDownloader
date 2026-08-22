import json
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.download_modes import (
    MODE_AUDIO_THUMB,
    MODE_VIDEO_AUDIO_THUMB,
    MODE_VIDEO_THUMB,
    PART_AUDIO,
    PART_THUMB,
    PART_VIDEO,
    required_parts,
)
from core.downloader import (
    DOWNLOAD_ENGINE_ARIA2_FAST,
    DOWNLOAD_ENGINE_STABLE,
    DownloadOptions,
    SkipCurrentVideo,
    YTDLP_STAGE_DOWNLOAD,
    YtdlpExecutionError,
    YtdlpFailureKind,
)
from core.state_store import STATUS_DOWNLOADED, STATUS_ERROR, STATUS_NOT_DOWNLOADED


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"
HYBRID_VIDEO_FORMAT_ID = "137"
HYBRID_AUDIO_FORMAT_ID = "140"


def main() -> int:
    _test_sequential_order_for_stable_and_fast()
    _test_video_audio_thumb_order_is_sequential()
    _test_audio_only_order_is_sequential()
    _test_fast_invokes_aria2_and_stable_excludes_it()
    _test_fast_has_no_convert_phase_or_source_queue()
    _test_fast_uses_cookie_lookahead_state()
    _test_same_validation_and_promotion_surface()
    _test_skip_retains_original_assigned_numbers()
    _test_failure_retains_original_assigned_numbers()
    _test_strict_format_failure_counts_once_and_continues()
    print("fast stable parity smoke passed")
    return 0


def _test_sequential_order_for_stable_and_fast() -> None:
    stable = _run_batch(DOWNLOAD_ENGINE_STABLE, MODE_VIDEO_THUMB, ("A", "B", "C"))
    fast = _run_batch(DOWNLOAD_ENGINE_ARIA2_FAST, MODE_VIDEO_THUMB, ("A", "B", "C"))
    expected = [
        "video:A",
        "thumb:A",
        "video:B",
        "thumb:B",
        "video:C",
        "thumb:C",
    ]
    _assert(stable.part_calls == expected, f"Stable order changed: {stable.part_calls}")
    _assert(fast.part_calls == expected, f"Fast order changed: {fast.part_calls}")
    _assert(stable.part_calls == fast.part_calls, "Stable and Fast part order diverged")


def _test_video_audio_thumb_order_is_sequential() -> None:
    stable = _run_batch(DOWNLOAD_ENGINE_STABLE, MODE_VIDEO_AUDIO_THUMB, ("A", "B"))
    fast = _run_batch(DOWNLOAD_ENGINE_ARIA2_FAST, MODE_VIDEO_AUDIO_THUMB, ("A", "B"))
    expected = [
        "video:A",
        "audio:A",
        "thumb:A",
        "video:B",
        "audio:B",
        "thumb:B",
    ]
    _assert(stable.part_calls == expected, f"Stable video+audio+thumb order changed: {stable.part_calls}")
    _assert(fast.part_calls == expected, f"Fast video+audio+thumb order changed: {fast.part_calls}")


def _test_audio_only_order_is_sequential() -> None:
    stable = _run_batch(DOWNLOAD_ENGINE_STABLE, MODE_AUDIO_THUMB, ("A", "B", "C"))
    fast = _run_batch(DOWNLOAD_ENGINE_ARIA2_FAST, MODE_AUDIO_THUMB, ("A", "B", "C"))
    expected = [
        "audio:A",
        "thumb:A",
        "audio:B",
        "thumb:B",
        "audio:C",
        "thumb:C",
    ]
    _assert(stable.part_calls == expected, f"Stable audio-only order changed: {stable.part_calls}")
    _assert(fast.part_calls == expected, f"Fast audio-only order changed: {fast.part_calls}")


def _test_fast_invokes_aria2_and_stable_excludes_it() -> None:
    stable = _run_batch(DOWNLOAD_ENGINE_STABLE, MODE_VIDEO_THUMB, ("A",))
    fast = _run_batch(DOWNLOAD_ENGINE_ARIA2_FAST, MODE_VIDEO_THUMB, ("A",))
    stable_video = _commands_for_part(stable, PART_VIDEO)[0]
    fast_video = next(
        command
        for command in _commands_for_part(fast, PART_VIDEO)
        if "--load-info-json" in command and _option_value(command, "-f") == HYBRID_VIDEO_FORMAT_ID
    )
    fast_thumb = _commands_for_part(fast, PART_THUMB)[0]
    _assert("--downloader" not in stable_video, "Stable video command unexpectedly used aria2")
    _assert("--downloader" in fast_video, "Fast video command missed aria2")
    _assert("--downloader-args" in fast_video, "Fast video command missed aria2 args")
    _assert("--downloader" not in fast_thumb, "Fast thumbnail command inherited aria2")

    fast_audio = _run_batch(DOWNLOAD_ENGINE_ARIA2_FAST, MODE_AUDIO_THUMB, ("A",))
    audio_command = _commands_for_part(fast_audio, PART_AUDIO)[0]
    thumb_command = _commands_for_part(fast_audio, PART_THUMB)[0]
    _assert("--downloader" in audio_command, "Fast direct audio command missed aria2")
    _assert("--downloader" not in thumb_command, "Fast audio thumbnail command inherited aria2")


def _test_fast_has_no_convert_phase_or_source_queue() -> None:
    result = _run_batch(DOWNLOAD_ENGINE_ARIA2_FAST, MODE_VIDEO_THUMB, ("A", "B", "C"))
    forbidden_prefixes = ("convert:", "phase1:", "phase2:", "transcode:", "source:")
    for call in result.part_calls + result.events:
        _assert(not call.startswith(forbidden_prefixes), f"Fast still has old phase call: {call}")
    _assert(not any("queued" in line.lower() for line in result.logs), "Fast logged source queue behavior")


def _test_fast_uses_cookie_lookahead_state() -> None:
    result = _run_batch(DOWNLOAD_ENGINE_ARIA2_FAST, MODE_VIDEO_THUMB, ("A", "B"), cookies_enabled=True)
    _assert(
        result.lookahead_calls == [(DOWNLOAD_ENGINE_ARIA2_FAST, "A"), (DOWNLOAD_ENGINE_ARIA2_FAST, "B")],
        f"Fast did not enter video attempt lookahead state: {result.lookahead_calls}",
    )


def _test_same_validation_and_promotion_surface() -> None:
    stable = _run_batch(DOWNLOAD_ENGINE_STABLE, MODE_VIDEO_THUMB, ("A", "B"))
    fast = _run_batch(DOWNLOAD_ENGINE_ARIA2_FAST, MODE_VIDEO_THUMB, ("A", "B"))
    _assert(stable.validations == ["A", "B"], f"Stable validation surface changed: {stable.validations}")
    _assert(fast.validations == stable.validations, "Fast validation surface diverged from Stable")
    _assert(
        stable.promotions == ["video:A", "thumb:A", "video:B", "thumb:B"],
        f"Stable promotion surface changed: {stable.promotions}",
    )
    _assert(fast.promotions == stable.promotions, "Fast promotion surface diverged from Stable")


def _test_skip_retains_original_assigned_numbers() -> None:
    result = _run_batch(
        DOWNLOAD_ENGINE_ARIA2_FAST,
        MODE_VIDEO_THUMB,
        ("A", "B", "C"),
        skip_video_ids={"B"},
        file_start_number=10,
    )
    _assert(result.stems == {"A": "010 A", "B": "011 B", "C": "012 C"}, f"Skip renumbered stems: {result.stems}")
    _assert(result.part_calls == ["video:A", "thumb:A", "video:B", "video:C", "thumb:C"], result.part_calls)
    _assert(any("[SKIP] Skipped: 1" in line for line in result.logs), "Skip count was not logged once")


def _test_failure_retains_original_assigned_numbers() -> None:
    result = _run_batch(
        DOWNLOAD_ENGINE_ARIA2_FAST,
        MODE_VIDEO_THUMB,
        ("A", "B", "C"),
        fail_video_ids={"B"},
        file_start_number=10,
    )
    _assert(result.stems == {"A": "010 A", "B": "011 B", "C": "012 C"}, f"Failure renumbered stems: {result.stems}")
    _assert(result.part_calls == ["video:A", "thumb:A", "video:B", "video:C", "thumb:C"], result.part_calls)
    _assert(any("[ERROR] Failed: 1" in line for line in result.logs), "Failure count was not logged once")


def _test_strict_format_failure_counts_once_and_continues() -> None:
    result = _run_batch(
        DOWNLOAD_ENGINE_ARIA2_FAST,
        MODE_VIDEO_THUMB,
        ("A", "B", "C"),
        fail_video_ids={"B"},
        failure_kind=YtdlpFailureKind.FORMAT_UNAVAILABLE,
    )
    b_video_commands = [
        command
        for part, video_id, command in result.commands
        if part == PART_VIDEO and video_id == "B"
    ]
    _assert(len(b_video_commands) == 1, f"Strict format failure retried B unexpectedly: {len(b_video_commands)}")
    command_text = " ".join(str(value) for value in b_video_commands[0])
    _assert(downloader.PREMIERE_SAFE_VIDEO_FORMAT in command_text, "Fast strict failure did not use strict selector")
    _assert("bestvideo" not in command_text, "Fast strict failure used unrestricted bestvideo")
    _assert("bestaudio" not in command_text, "Fast strict failure used unrestricted bestaudio")
    _assert("libx264" not in command_text, "Fast strict failure triggered full transcode")
    _assert("video:C" in result.part_calls, "Later video did not continue after strict format failure")
    _assert(any("[ERROR] Failed: 1" in line for line in result.logs), "Strict format failure did not count once")
    _assert(not any("[SKIP] Skipped: 1" in line for line in result.logs), "Strict format failure incremented skipped count")


def _run_batch(
    engine: str,
    mode: str,
    video_ids: tuple[str, ...],
    *,
    fail_video_ids: set[str] | None = None,
    skip_video_ids: set[str] | None = None,
    failure_kind: YtdlpFailureKind = YtdlpFailureKind.FORMAT_UNAVAILABLE,
    file_start_number: int = 1,
    cookies_enabled: bool = False,
):
    fail_video_ids = fail_video_ids or set()
    skip_video_ids = skip_video_ids or set()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        videos = [_video(video_id) for video_id in video_ids]
        entries: dict[str, dict] = {}
        result = SimpleNamespace(
            part_calls=[],
            events=[],
            commands=[],
            validations=[],
            promotions=[],
            states=[],
            reconciles=[],
            statuses=[],
            logs=[],
            stems={},
            lookahead_calls=[],
        )
        snapshot_video_ids: dict[str, str] = {}
        cookie_path = root / "cookies.txt"
        if cookies_enabled:
            cookie_path.write_text("cookie-data", encoding="utf-8")
        options = DownloadOptions(
            base_folder=str(root / "downloads"),
            channel_id=CHANNEL_ID,
            channel_name=CHANNEL_NAME,
            cookies_enabled=cookies_enabled,
            cookies_path=str(cookie_path) if cookies_enabled else "",
            download_mode=mode,
            download_engine=engine,
            file_start_number=file_start_number,
        )

        def fake_prepare_runtime(current_options, log, cancel_controller=None):
            requested = current_options.download_engine == DOWNLOAD_ENGINE_ARIA2_FAST
            return downloader._Aria2RuntimeValidation(requested, requested, paths.aria2)

        def fake_missing_parts(current_options, video, output_paths, mode_parts):
            result.stems[video.video_id] = video.sanitized_filename_base
            return tuple(mode_parts)

        def fake_get_entry(channel_id, video_id, save_base_folder=None):
            return _entry(entries, video_id)

        def fake_update_part(channel_id, channel_name, save_base_folder, video, output_paths, part, status, download_mode):
            entry = _entry(entries, video.video_id)
            entry[_status_key(part)] = status
            result.states.append(f"state:{part}:{video.video_id}:{status}")

        def fake_reconcile(channel_id, channel_name, save_base_folder, video, output_paths, download_mode, run_parts=None):
            entry = _entry(entries, video.video_id)
            old_status = entry.get("status", STATUS_NOT_DOWNLOADED)
            new_status = downloader.get_effective_status(entry, download_mode)
            entry["status"] = new_status
            result.reconciles.append(f"reconcile:{video.video_id}:{new_status}:{','.join(run_parts or ())}")
            return old_status, new_status

        def fake_ready(path, cancel_controller=None):
            return Path(path).exists()

        def fake_validate(path, log, delete_invalid, cancel_controller):
            video_id = _video_id_from_hybrid_path(path, snapshot_video_ids)
            result.validations.append(video_id)

        def fake_promote(source_path, final_path, log, replace_existing=False, cancel_controller=None):
            final = Path(final_path)
            final.parent.mkdir(parents=True, exist_ok=True)
            source = Path(source_path)
            data = source.read_bytes() if source.exists() else b"data"
            final.write_bytes(data or b"data")
            result.promotions.append(f"{_part_from_suffix(final.suffix)}:{_video_id_from_final(final, result.stems)}")

        def fake_extract_mp3(source_video_path, temp_dir, final_audio_path, log=None, cancel_controller=None):
            video_id = _video_id_from_final(Path(source_video_path), result.stems)
            result.part_calls.append(f"audio:{video_id}")
            Path(final_audio_path).parent.mkdir(parents=True, exist_ok=True)
            Path(final_audio_path).write_bytes(b"mp3")

        def fake_run_ytdlp(command, current_options, log, cancel_controller=None, cookie_retry_state=None):
            part = downloader._current_ytdlp_part()
            load_info_json = _option_value(command, "--load-info-json")
            video_id = snapshot_video_ids.get(load_info_json) or _video_id_from_command(command)
            result.commands.append((part, video_id, list(command)))
            is_hybrid_metadata = "--write-info-json" in command
            if part in {PART_AUDIO, PART_THUMB} or (
                part == PART_VIDEO
                and (is_hybrid_metadata or not load_info_json)
            ):
                result.part_calls.append(f"{part}:{video_id}")
            if part == PART_VIDEO and video_id in skip_video_ids:
                raise SkipCurrentVideo("skip requested")
            if part == PART_VIDEO and video_id in fail_video_ids:
                raise YtdlpExecutionError(
                    1,
                    "Requested format is not available",
                    ["ERROR: Requested format is not available"],
                    failure_kind=failure_kind,
                    stage=YTDLP_STAGE_DOWNLOAD,
                    part=PART_VIDEO,
                    command=command,
                )
            if is_hybrid_metadata:
                output_template = _option_value(command, "-o")
                info_path = Path(output_template.replace("%(ext)s", "info.json"))
                info_path.parent.mkdir(parents=True, exist_ok=True)
                info_path.write_text(json.dumps(_hybrid_info(video_id)), encoding="utf-8")
                snapshot_video_ids[str(info_path)] = video_id
                return
            if load_info_json:
                output_template = _option_value(command, "-o")
                format_id = _option_value(command, "-f")
                suffix = "mp4" if format_id == HYBRID_VIDEO_FORMAT_ID else "m4a"
                output_path = Path(output_template.replace("%(ext)s", suffix))
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(format_id.encode("ascii"))
                return
            _write_staged_output(command, part, video_id)

        def fake_ffmpeg(command, *, operation, cancel_controller=None, progress_duration_seconds=None):
            Path(command[-1]).write_bytes(b"merged")
            return ""

        original_attempt_state = downloader._video_attempt_state_for_batch

        def wrapped_attempt_state(videos_arg, current_index, video_id, current_options, batch_state, log, cancel_controller):
            result.lookahead_calls.append((current_options.download_engine, str(video_id)))
            return original_attempt_state(
                videos_arg,
                current_index,
                video_id,
                current_options,
                batch_state,
                log,
                cancel_controller,
            )

        with ExitStack() as stack:
            stack.enter_context(_patched_runtime(paths))
            stack.enter_context(_patched_attr(downloader, "validate_download_environment", lambda options: None))
            stack.enter_context(_patched_attr(downloader, "_call_runtime_tool_summary", lambda *args, **kwargs: None))
            stack.enter_context(_patched_attr(downloader, "_prepare_media_downloader_runtime", fake_prepare_runtime))
            stack.enter_context(_patched_attr(downloader, "_missing_parts_for_current_paths", fake_missing_parts))
            stack.enter_context(_patched_attr(downloader, "get_video_entry", fake_get_entry))
            stack.enter_context(_patched_attr(downloader, "update_video_part_state", fake_update_part))
            stack.enter_context(_patched_attr(downloader, "reconcile_downloaded_item_state", fake_reconcile))
            stack.enter_context(_patched_attr(downloader, "_premiere_safe_mp4_ready_for_download", fake_ready))
            stack.enter_context(_patched_attr(downloader, "_validate_premiere_safe_mp4_for_download", fake_validate))
            stack.enter_context(_patched_attr(downloader, "_atomic_promote_with_retry", fake_promote))
            stack.enter_context(_patched_attr(downloader, "_extract_mp3_from_video", fake_extract_mp3))
            stack.enter_context(_patched_attr(downloader, "_run_ytdlp_with_retries", fake_run_ytdlp))
            stack.enter_context(_patched_attr(downloader, "_run_ffmpeg_command", fake_ffmpeg))
            stack.enter_context(_patched_attr(downloader, "_video_attempt_state_for_batch", wrapped_attempt_state))
            downloader.download_items(
                videos,
                options,
                result.logs.append,
                result.statuses.append,
                progress_callback=lambda event: result.events.append(f"progress:{event.kind}:{event.phase}"),
            )

        return result


def _video(video_id: str):
    return SimpleNamespace(
        video_id=video_id,
        title=video_id,
        sanitized_filename_base=video_id,
        thumbnail_url="",
        status=STATUS_NOT_DOWNLOADED,
    )


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


def _entry(entries: dict[str, dict], video_id: str) -> dict:
    return entries.setdefault(
        video_id,
        {
            "video_status": STATUS_NOT_DOWNLOADED,
            "audio_status": STATUS_NOT_DOWNLOADED,
            "thumb_status": STATUS_NOT_DOWNLOADED,
            "status": STATUS_NOT_DOWNLOADED,
        },
    )


def _status_key(part: str) -> str:
    return {
        PART_VIDEO: "video_status",
        PART_AUDIO: "audio_status",
        PART_THUMB: "thumb_status",
    }[part]


def _commands_for_part(result, part: str) -> list[list[str]]:
    return [command for command_part, _video_id, command in result.commands if command_part == part]


def _option_value(command: list[str], option: str) -> str:
    for index, value in enumerate(command):
        if value == option and index + 1 < len(command):
            return str(command[index + 1])
    return ""


def _video_id_from_command(command: list[str]) -> str:
    for value in command:
        text = str(value)
        marker = "watch?v="
        if marker in text:
            return text.split(marker, 1)[1].split("&", 1)[0]
    output_template = _option_value(command, "-o")
    return Path(output_template).stem.split(".", 1)[0] if output_template else "unknown"


def _write_staged_output(command: list[str], part: str, video_id: str) -> None:
    output_template = _option_value(command, "-o")
    staging_dir = Path(output_template).parent
    staging_dir.mkdir(parents=True, exist_ok=True)
    suffix = {
        PART_VIDEO: ".mp4",
        PART_AUDIO: ".mp3",
        PART_THUMB: ".jpg",
    }.get(part, ".bin")
    (staging_dir / f"{video_id}{suffix}").write_bytes(part.encode("utf-8") or b"data")


def _video_id_from_staged_path(path: Path) -> str:
    return Path(path).stem


def _video_id_from_hybrid_path(path: Path, snapshot_video_ids: dict[str, str]) -> str:
    candidate = Path(path).resolve(strict=False)
    for info_path, video_id in snapshot_video_ids.items():
        if Path(info_path).parent.resolve(strict=False) == candidate.parent:
            return video_id
    return _video_id_from_staged_path(path)


def _hybrid_info(video_id: str) -> dict:
    return {
        "id": video_id,
        "duration": 60.0,
        "requested_formats": [
            {
                "format_id": HYBRID_VIDEO_FORMAT_ID,
                "ext": "mp4",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "height": 1080,
                "filesize": 9_000_000,
            },
            {
                "format_id": HYBRID_AUDIO_FORMAT_ID,
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "filesize": 1_000_000,
            },
        ],
    }


def _video_id_from_final(path: Path, stems: dict[str, str]) -> str:
    final_stem = path.stem
    for video_id, stem in stems.items():
        if stem == final_stem:
            return video_id
    return final_stem


def _part_from_suffix(suffix: str) -> str:
    return {
        ".mp4": PART_VIDEO,
        ".mp3": PART_AUDIO,
        ".jpg": PART_THUMB,
    }.get(suffix.lower(), "unknown")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
