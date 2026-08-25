import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.download_modes import MODE_VIDEO_AUDIO_THUMB, MODE_VIDEO_THUMB, PART_THUMB, PART_VIDEO
from core.downloader import DOWNLOAD_ENGINE_ARIA2_FAST, DOWNLOAD_ENGINE_STABLE, DownloadError, DownloadOptions
from core.file_status import build_output_paths
from core.state_store import STATUS_DOWNLOADED


def main() -> int:
    _test_validate_file_start_number()
    _test_number_formatting()
    _test_repeated_prefix_cleanup_examples()
    _test_fixed_assignment_consumes_skips_and_failures()
    _test_same_object_retry_numbering()
    _test_corrupted_fallback_numbering()
    _test_legitimate_numeric_title_is_preserved()
    _test_stable_and_fast_share_numbering_helper()
    _test_numbered_stem_and_shared_part_paths()
    _test_downloaded_state_survives_number_change()
    print("numbered output names smoke passed")
    return 0


def _test_validate_file_start_number() -> None:
    required_values = (None, "", "   ")
    for value in required_values:
        try:
            downloader.validate_file_start_number(value)
        except DownloadError as exc:
            _assert(str(exc) == downloader.FILE_START_NUMBER_REQUIRED_MESSAGE, f"{value!r} did not raise required")
        else:
            raise AssertionError(f"{value!r} did not fail")

    invalid_values = (0, "0", -1, "-1", "1.5", "abc", True)
    for value in invalid_values:
        try:
            downloader.validate_file_start_number(value)
        except DownloadError as exc:
            _assert(str(exc) == downloader.FILE_START_NUMBER_INVALID_MESSAGE, f"{value!r} did not raise invalid")
        else:
            raise AssertionError(f"{value!r} did not fail")

    for value, expected in (("1", 1), ("001", 1), (1, 1), ("1000", 1000)):
        _assert(downloader.validate_file_start_number(value) == expected, f"{value!r} normalized incorrectly")


def _test_number_formatting() -> None:
    expected = {
        1: "001",
        9: "009",
        51: "051",
        999: "999",
        1000: "1000",
    }
    for number, text in expected.items():
        _assert(downloader._format_output_number(number) == text, f"{number} formatted incorrectly")


def _test_repeated_prefix_cleanup_examples() -> None:
    examples = {
        "001 Title": "Title",
        "051 001 Title": "Title",
        "1000 Title": "Title",
        "1234 051 001 Title": "Title",
        "Title": "Title",
        "": "",
    }
    for value, expected in examples.items():
        actual = downloader._strip_existing_output_number_prefixes(value)
        _assert(actual == expected, f"{value!r} cleaned to {actual!r}, expected {expected!r}")


def _test_fixed_assignment_consumes_skips_and_failures() -> None:
    assignments = [downloader._assigned_output_number(101, index) for index in range(4)]
    _assert(assignments == [101, 102, 103, 104], f"assignment shifted: {assignments}")
    outcomes = {"A": assignments[0], "B_skip": assignments[1], "C_fail": assignments[2], "D_success": assignments[3]}
    _assert(outcomes["D_success"] == 104, "D did not retain 104 after skip/failure")
    try:
        downloader._assigned_output_number(101, -1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative selected_index did not fail")


def _test_same_object_retry_numbering() -> None:
    with TemporaryDirectory(prefix="numbered_retry_") as temp_dir:
        options = _options(temp_dir, start_number=1)
        video = _video("retry-video", "Title")

        _assigned, first_stem, _paths = downloader._prepare_numbered_output_for_video(video, options, 0)
        options.file_start_number = 51
        _assigned, second_stem, _paths = downloader._prepare_numbered_output_for_video(video, options, 0)
        options.file_start_number = 101
        _assigned, third_stem, _paths = downloader._prepare_numbered_output_for_video(video, options, 0)

    _assert(first_stem == "001 Title", f"first stem wrong: {first_stem}")
    _assert(second_stem == "051 Title", f"second stem accumulated prefix: {second_stem}")
    _assert(third_stem == "101 Title", f"third stem accumulated prefix: {third_stem}")
    _assert(second_stem != "051 001 Title", "second stem reused previous numbered output")
    _assert("101 051" not in third_stem, "third retry retained previous prefixes")


def _test_corrupted_fallback_numbering() -> None:
    with TemporaryDirectory(prefix="numbered_corrupt_") as temp_dir:
        options = _options(temp_dir, start_number=201)
        video = _video("corrupt-video", "", sanitized_filename_base="101 051 001 Title")
        _assigned, stem, _paths = downloader._prepare_numbered_output_for_video(video, options, 0)

    _assert(stem == "201 Title", f"corrupted fallback was not cleaned: {stem}")


def _test_legitimate_numeric_title_is_preserved() -> None:
    numeric_title = "2024\u5e74\u306e\u51fa\u6765\u4e8b"
    with TemporaryDirectory(prefix="numbered_numeric_") as temp_dir:
        options = _options(temp_dir, start_number=51)
        video = _video("numeric-video", numeric_title, sanitized_filename_base="001 old value")
        _assigned, stem, _paths = downloader._prepare_numbered_output_for_video(video, options, 0)

    _assert(stem == f"051 {numeric_title}", f"canonical numeric title was stripped: {stem}")


def _test_stable_and_fast_share_numbering_helper() -> None:
    with TemporaryDirectory(prefix="numbered_engine_") as temp_dir:
        stable_options = _options(temp_dir, start_number=7, engine=DOWNLOAD_ENGINE_STABLE)
        fast_options = _options(temp_dir, start_number=7, engine=DOWNLOAD_ENGINE_ARIA2_FAST)
        stable_video = _video("stable-video", "Shared Title")
        fast_video = _video("fast-video", "Shared Title")
        _assigned, stable_stem, _paths = downloader._prepare_numbered_output_for_video(stable_video, stable_options, 0)
        _assigned, fast_stem, _paths = downloader._prepare_numbered_output_for_video(fast_video, fast_options, 0)

    _assert(stable_stem == "007 Shared Title", f"stable stem wrong: {stable_stem}")
    _assert(fast_stem == stable_stem, f"fast stem diverged from stable: {fast_stem}")


def _test_numbered_stem_and_shared_part_paths() -> None:
    with TemporaryDirectory(prefix="numbered_paths_") as temp_dir:
        options = _options(temp_dir, start_number=51, download_mode=MODE_VIDEO_AUDIO_THUMB)
        video = _video("video-1", "Example: title?.mp4")
        assigned, stem, paths = downloader._prepare_numbered_output_for_video(video, options, 0)

    _assert(assigned == 51, "assigned number mismatch")
    _assert(stem.startswith("051 "), f"number prefix missing: {stem}")
    _assert(paths.video_path.name == f"{stem}.mp4", "video name did not use numbered stem")
    _assert(paths.audio_path.name == f"{stem}.mp3", "audio name did not use numbered stem")
    _assert(paths.thumb_path.name == f"{stem}.jpg", "thumb name did not use numbered stem")
    _assert(video.sanitized_filename_base == stem, "video canonical stem was not updated")


def _test_downloaded_state_survives_number_change() -> None:
    with TemporaryDirectory(prefix="numbered_skip_") as temp_dir:
        options = _options(temp_dir, start_number=51)
        video = _video("video-old", "Title")
        _assigned, stem, paths = downloader._prepare_numbered_output_for_video(video, options, 0)
        old_paths = build_output_paths(temp_dir, "Channel", "001 Title")
        old_paths.video_path.parent.mkdir(parents=True, exist_ok=True)
        old_paths.thumb_path.parent.mkdir(parents=True, exist_ok=True)
        old_paths.video_path.write_bytes(b"old video")
        old_paths.thumb_path.write_bytes(b"old thumb")
        _assert(stem == "051 Title", "unexpected new numbered stem")

        old_get = downloader.get_video_entry
        old_update = downloader.update_video_part_state
        updates: list[tuple[str, str]] = []
        old_video_exists = False
        old_thumb_exists = False
        new_video_exists = False
        new_thumb_exists = False
        try:
            downloader.get_video_entry = lambda *_args, **_kwargs: {
                "video_status": STATUS_DOWNLOADED,
                "thumb_status": STATUS_DOWNLOADED,
            }
            downloader.update_video_part_state = lambda *_args, **_kwargs: updates.append((_args[5], _args[6]))
            missing = downloader._missing_parts_for_current_paths(
                options,
                video,
                paths,
                (PART_VIDEO, PART_THUMB),
            )
            old_video_exists = old_paths.video_path.exists()
            old_thumb_exists = old_paths.thumb_path.exists()
            new_video_exists = paths.video_path.exists()
            new_thumb_exists = paths.thumb_path.exists()
        finally:
            downloader.get_video_entry = old_get
            downloader.update_video_part_state = old_update

    _assert(missing == (), f"new numbered paths overrode downloaded state: {missing}")
    _assert(updates == [], f"downloaded state was downgraded after numbering changed: {updates}")
    _assert(old_video_exists, "old numbered video was renamed or deleted")
    _assert(old_thumb_exists, "old numbered thumbnail was renamed or deleted")
    _assert(not new_video_exists, "new numbered video was created for a downloaded item")
    _assert(not new_thumb_exists, "new numbered thumbnail was created for a downloaded item")


def _options(
    base_folder: str,
    *,
    start_number: int,
    download_mode: str = MODE_VIDEO_THUMB,
    engine: str = DOWNLOAD_ENGINE_STABLE,
) -> DownloadOptions:
    return DownloadOptions(
        base_folder=base_folder,
        channel_id="channel",
        channel_name="Channel",
        download_mode=download_mode,
        download_engine=engine,
        file_start_number=start_number,
    )


def _video(video_id: str, title: str, *, sanitized_filename_base: str | None = None):
    return SimpleNamespace(
        video_id=video_id,
        title=title,
        sanitized_filename_base=title if sanitized_filename_base is None else sanitized_filename_base,
        thumbnail_url="",
        status="",
        display_order=1,
    )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
