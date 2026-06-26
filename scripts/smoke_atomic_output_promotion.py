import inspect
import sys
import tempfile
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.download_modes import MODE_VIDEO_THUMB, PART_VIDEO
from core.downloader import DownloadController, DownloadOptions
from core.state_store import STATUS_DOWNLOADED, STATUS_ERROR


def main() -> int:
    _test_staging_location_and_cleanup()
    _test_same_directory_tree_accepted_with_parent_device_check()
    _test_cross_filesystem_parent_mismatch_rejected()
    _test_existing_file_preserved_on_permanent_promotion_failure()
    _test_temporary_lock_then_success()
    _test_replace_success_temporary_verification_failure()
    _test_replace_success_permanent_verification_failure()
    _test_cancellation_during_retry()
    _test_cancellation_during_verification()
    _test_mp4_validation_before_promotion()
    _test_audio_promotion_failures_preserve_existing()
    _test_thumbnail_promotion_paths()
    _test_same_filesystem_enforced()
    _test_no_destination_unlink_or_move_fallback()
    _test_state_update_follows_part_success()
    _test_staging_cleanup_all_terminal_paths()
    print("atomic output promotion smoke passed")
    return 0


def _test_staging_location_and_cleanup() -> None:
    with TemporaryDirectory(prefix="atomic_stage_") as temp_dir:
        channel_dir = Path(temp_dir) / "Channel"
        channel_dir.mkdir()
        sentinel = channel_dir / "video"
        sentinel.mkdir()
        raw_title = "Raw Video Title"
        paths = []
        with downloader._media_staging_directory(channel_dir, "video-1", lambda _message: None) as first:
            with downloader._media_staging_directory(channel_dir, "video-1", lambda _message: None) as second:
                paths.extend([first, second])
                _assert(first != second, "staging directories were reused")
                for path in paths:
                    _assert(path.parent == channel_dir, "staging directory is not directly under channel dir")
                    _assert(path.name.startswith(".s9h-stage-video-1-"), f"bad staging prefix: {path.name}")
                    _assert(raw_title not in path.name, "staging name contains raw title")
                    if not _is_relative_to(channel_dir, Path(tempfile.gettempdir())):
                        _assert(
                            not _is_relative_to(path, Path(tempfile.gettempdir())),
                            "staging used system temp outside channel dir",
                        )
                    (path / "attempt.tmp").write_bytes(b"x")
            _assert(not second.exists(), "second staging directory was not cleaned")
        _assert(not first.exists(), "first staging directory was not cleaned")
        _assert(sentinel.exists(), "cleanup removed an output directory")


def _test_same_directory_tree_accepted_with_parent_device_check() -> None:
    with TemporaryDirectory(prefix="atomic_same_tree_") as temp_dir:
        channel_dir = Path(temp_dir) / "Channel"
        stage_dir = channel_dir / ".s9h-stage-test-abc"
        video_dir = channel_dir / "video"
        stage_dir.mkdir(parents=True)
        video_dir.mkdir()
        candidate = stage_dir / "candidate.mp4"
        final = video_dir / "final.mp4"
        candidate.write_bytes(b"NEW")
        final.write_bytes(b"OLD")
        real_stat = Path.stat
        real_replace = downloader.os.replace
        replace_calls = []

        def fake_stat(path_self, *args, **kwargs):
            path = Path(path_self)
            real = real_stat(path_self, *args, **kwargs)
            if path == candidate:
                return SimpleNamespace(st_dev=100, st_size=3, st_mode=real.st_mode)
            if path == stage_dir:
                return SimpleNamespace(st_dev=200, st_size=0, st_mode=real.st_mode)
            if path == video_dir:
                return SimpleNamespace(st_dev=200, st_size=0, st_mode=real.st_mode)
            if path == final:
                return SimpleNamespace(st_dev=300, st_size=len(final.read_bytes()), st_mode=real.st_mode)
            return real_stat(path_self, *args, **kwargs)

        def tracking_replace(source, target):
            replace_calls.append((Path(source), Path(target)))
            real_replace(source, target)

        with _patched_attr(Path, "stat", fake_stat), _patched_attr(downloader.os, "replace", tracking_replace):
            _assert(
                downloader._paths_share_filesystem(candidate, video_dir),
                "same staging/output tree was rejected",
            )
            downloader._atomic_promote_with_retry(candidate, final, replace_existing=True)

        _assert(len(replace_calls) == 1, "os.replace was not reached exactly once")
        _assert(final.read_bytes() == b"NEW", "same-tree promotion did not succeed")


def _test_cross_filesystem_parent_mismatch_rejected() -> None:
    with TemporaryDirectory(prefix="atomic_cross_tree_") as temp_dir:
        root = Path(temp_dir)
        source_dir = root / "stage"
        destination_dir = root / "video"
        source_dir.mkdir()
        destination_dir.mkdir()
        candidate = source_dir / "candidate.mp4"
        final = destination_dir / "final.mp4"
        candidate.write_bytes(b"NEW")
        final.write_bytes(b"OLD")
        real_stat = Path.stat
        replace_calls = []
        move_calls = []

        def fake_stat(path_self, *args, **kwargs):
            path = Path(path_self)
            real = real_stat(path_self, *args, **kwargs)
            if path == candidate:
                return SimpleNamespace(st_dev=999, st_size=3, st_mode=real.st_mode)
            if path == source_dir:
                return SimpleNamespace(st_dev=100, st_size=0, st_mode=real.st_mode)
            if path == destination_dir:
                return SimpleNamespace(st_dev=200, st_size=0, st_mode=real.st_mode)
            if path == final:
                return SimpleNamespace(st_dev=200, st_size=3, st_mode=real.st_mode)
            return real_stat(path_self, *args, **kwargs)

        with _patched_attr(Path, "stat", fake_stat), _patched_attr(
            downloader.os,
            "replace",
            lambda *_args: replace_calls.append("replace"),
        ), _patched_attr(
            downloader.shutil,
            "move",
            lambda *_args, **_kwargs: move_calls.append("move"),
        ):
            _assert(
                not downloader._paths_share_filesystem(candidate, destination_dir),
                "cross-filesystem parent mismatch was accepted",
            )
            try:
                downloader._atomic_promote_with_retry(candidate, final, replace_existing=True)
            except downloader.FileOperationError as exc:
                _assert(exc.operation == "promote", f"wrong operation for filesystem mismatch: {exc.operation}")
            else:
                raise AssertionError("cross-filesystem parent mismatch did not fail")

        _assert(replace_calls == [], "os.replace was called after cross-filesystem rejection")
        _assert(move_calls == [], "shutil.move fallback was used")
        _assert(final.read_bytes() == b"OLD", "old destination changed after cross-filesystem rejection")


def _test_existing_file_preserved_on_permanent_promotion_failure() -> None:
    with TemporaryDirectory(prefix="atomic_fail_") as temp_dir:
        root = Path(temp_dir)
        final = root / "final.mp4"
        staged = root / ".s9h-stage-video" / "new.mp4"
        staged.parent.mkdir()
        final.write_bytes(b"OLD_VALID_FILE")
        staged.write_bytes(b"NEW_VALID_FILE")
        replace_calls = []
        verify_calls = []

        def fail_replace(_source, _target):
            replace_calls.append((Path(_source), Path(_target), final.read_bytes()))
            raise PermissionError("locked")

        with _patched_attr(downloader.os, "replace", fail_replace), _patched_attr(
            downloader,
            "_sleep_with_cancel",
            lambda _seconds, _controller: None,
        ), _patched_attr(
            downloader,
            "_verify_promoted_file_with_retry",
            lambda *_args, **_kwargs: verify_calls.append("verify"),
        ):
            try:
                downloader._atomic_promote_with_retry(staged, final, replace_existing=True)
            except downloader.FileOperationError as exc:
                _assert(exc.operation == "promote", f"wrong operation for permanent replace failure: {exc.operation}")
            else:
                raise AssertionError("permanent replace failure did not raise FileOperationError")

        _assert(replace_calls, "os.replace was not attempted")
        _assert(verify_calls == [], "verification ran even though replace never succeeded")
        _assert(all(bytes_seen == b"OLD_VALID_FILE" for *_rest, bytes_seen in replace_calls), "old final changed during retries")
        _assert(final.read_bytes() == b"OLD_VALID_FILE", "old final was not preserved")
        _assert(staged.read_bytes() == b"NEW_VALID_FILE", "staged source was removed before cleanup")


def _test_temporary_lock_then_success() -> None:
    with TemporaryDirectory(prefix="atomic_retry_") as temp_dir:
        root = Path(temp_dir)
        final = root / "final.mp3"
        staged = root / "stage" / "new.mp3"
        staged.parent.mkdir()
        final.write_bytes(b"OLD")
        staged.write_bytes(b"NEW")
        real_replace = downloader.os.replace
        calls = []
        verify_calls = []
        logs = []

        def flaky_replace(source, target):
            calls.append(final.read_bytes())
            if len(calls) < 3:
                raise PermissionError("temporarily locked")
            real_replace(source, target)

        real_verify = downloader._verify_promoted_file_once

        def verify_once(source, target):
            verify_calls.append((Path(source), Path(target)))
            real_verify(source, target)

        with _patched_attr(downloader.os, "replace", flaky_replace), _patched_attr(
            downloader,
            "_sleep_with_cancel",
            lambda _seconds, _controller: None,
        ), _patched_attr(
            downloader,
            "_verify_promoted_file_once",
            verify_once,
        ):
            downloader._atomic_promote_with_retry(staged, final, logs.append, replace_existing=True)

        _assert(len(calls) == 3, f"replace call count was wrong: {len(calls)}")
        _assert(calls[:2] == [b"OLD", b"OLD"], "old final did not remain through failed retries")
        _assert(final.read_bytes() == b"NEW", "final was not replaced on successful retry")
        _assert(not staged.exists(), "source still existed after successful replace")
        _assert(len(verify_calls) == 1, "verification did not run exactly once after successful replace")
        _assert(any("retry succeeded" in message for message in logs), "retry success warning was not logged")


def _test_replace_success_temporary_verification_failure() -> None:
    with TemporaryDirectory(prefix="atomic_verify_retry_") as temp_dir:
        root = Path(temp_dir)
        final = root / "final.mp4"
        staged = root / "stage" / "new.mp4"
        staged.parent.mkdir()
        final.write_bytes(b"OLD")
        staged.write_bytes(b"NEW")
        real_replace = downloader.os.replace
        replace_calls = []
        verify_calls = []

        def tracking_replace(source, target):
            replace_calls.append((Path(source), Path(target)))
            real_replace(source, target)

        def flaky_verify(_source, _target):
            verify_calls.append("verify")
            if len(verify_calls) == 1:
                raise OSError("delayed visibility")

        with _patched_attr(downloader.os, "replace", tracking_replace), _patched_attr(
            downloader,
            "_verify_promoted_file_once",
            flaky_verify,
        ), _patched_attr(
            downloader,
            "_sleep_with_cancel",
            lambda _seconds, _controller: None,
        ):
            downloader._atomic_promote_with_retry(staged, final, replace_existing=True)

        _assert(len(replace_calls) == 1, f"os.replace retried after success: {len(replace_calls)}")
        _assert(verify_calls == ["verify", "verify"], f"verification retry count was wrong: {verify_calls}")
        _assert(final.read_bytes() == b"NEW", "final did not contain promoted bytes")
        _assert(not staged.exists(), "source still existed after successful replace")


def _test_replace_success_permanent_verification_failure() -> None:
    with TemporaryDirectory(prefix="atomic_verify_fail_") as temp_dir:
        root = Path(temp_dir)
        final = root / "final.mp4"
        staged = root / "stage" / "new.mp4"
        staged.parent.mkdir()
        final.write_bytes(b"OLD")
        staged.write_bytes(b"NEW")
        real_replace = downloader.os.replace
        replace_calls = []

        def tracking_replace(source, target):
            replace_calls.append((Path(source), Path(target)))
            real_replace(source, target)

        with _patched_attr(downloader.os, "replace", tracking_replace), _patched_attr(
            downloader,
            "_verify_promoted_file_once",
            lambda _source, _target: (_ for _ in ()).throw(OSError("persistent stat failure")),
        ), _patched_attr(
            downloader,
            "_sleep_with_cancel",
            lambda _seconds, _controller: None,
        ):
            try:
                downloader._atomic_promote_with_retry(staged, final, replace_existing=True)
            except downloader.FileOperationError as exc:
                _assert(exc.operation == "verify_promoted_file", f"wrong verification operation: {exc.operation}")
            else:
                raise AssertionError("permanent verification failure did not raise")

        _assert(len(replace_calls) == 1, f"os.replace retried after verification failure: {len(replace_calls)}")
        _assert(final.read_bytes() == b"NEW", "promoted final was deleted or changed after verification failure")
        _assert(not staged.exists(), "source was recreated after successful replace")


def _test_cancellation_during_retry() -> None:
    with TemporaryDirectory(prefix="atomic_cancel_") as temp_dir:
        root = Path(temp_dir)
        final = root / "final.jpg"
        staged = root / "stage" / "new.jpg"
        staged.parent.mkdir()
        final.write_bytes(b"OLD")
        staged.write_bytes(b"NEW")
        controller = DownloadController()
        replace_calls = []
        verify_calls = []

        def locked_replace(_source, _target):
            replace_calls.append(time.monotonic())
            raise PermissionError("locked")

        def cancel_sleep(_seconds, sleep_controller):
            sleep_controller.request_cancel()
            downloader._raise_if_cancelled(sleep_controller)

        start = time.monotonic()
        with _patched_attr(downloader.os, "replace", locked_replace), _patched_attr(
            downloader,
            "_sleep_with_cancel",
            cancel_sleep,
        ), _patched_attr(
            downloader,
            "_verify_promoted_file_with_retry",
            lambda *_args, **_kwargs: verify_calls.append("verify"),
        ):
            try:
                downloader._atomic_promote_with_retry(staged, final, replace_existing=True, cancel_controller=controller)
            except downloader.DownloadCancelled:
                pass
            else:
                raise AssertionError("promotion retry did not honor cancellation")

        elapsed = time.monotonic() - start
        _assert(elapsed < 1, f"cancellation was too slow: {elapsed:.2f}s")
        _assert(len(replace_calls) == 1, "replace was attempted after cancellation")
        _assert(verify_calls == [], "verification began after cancellation before replace success")
        _assert(final.read_bytes() == b"OLD", "old final changed after cancellation")
        _assert(controller.is_cancel_requested(), "controller was not marked cancelled")


def _test_cancellation_during_verification() -> None:
    with TemporaryDirectory(prefix="atomic_verify_cancel_") as temp_dir:
        root = Path(temp_dir)
        final = root / "final.mp4"
        staged = root / "stage" / "new.mp4"
        staged.parent.mkdir()
        final.write_bytes(b"OLD")
        staged.write_bytes(b"NEW")
        controller = DownloadController()
        real_replace = downloader.os.replace
        replace_calls = []

        def tracking_replace(source, target):
            replace_calls.append((Path(source), Path(target)))
            real_replace(source, target)

        def cancelling_verify(_source, _target):
            controller.request_cancel()
            raise OSError("verification interrupted")

        with _patched_attr(downloader.os, "replace", tracking_replace), _patched_attr(
            downloader,
            "_verify_promoted_file_once",
            cancelling_verify,
        ), _patched_attr(
            downloader,
            "_sleep_with_cancel",
            lambda _seconds, sleep_controller: downloader._raise_if_cancelled(sleep_controller),
        ):
            try:
                downloader._atomic_promote_with_retry(staged, final, replace_existing=True, cancel_controller=controller)
            except downloader.DownloadCancelled:
                pass
            else:
                raise AssertionError("verification cancellation did not propagate")

        _assert(len(replace_calls) == 1, f"os.replace retried after verification cancellation: {len(replace_calls)}")
        _assert(final.read_bytes() == b"NEW", "promoted final was deleted after verification cancellation")
        _assert(not staged.exists(), "source still existed after successful replace")


def _test_mp4_validation_before_promotion() -> None:
    with TemporaryDirectory(prefix="atomic_mp4_invalid_") as temp_dir:
        root = Path(temp_dir)
        staging = root / "stage"
        staging.mkdir()
        final = root / "video.mp4"
        final.write_bytes(b"OLD_MP4")
        calls = []

        def fake_download(command, _options, _log, _controller=None, _state=None):
            calls.append("download")
            _output_path(command).write_bytes(b"BAD_MP4")

        def fake_validate(path, _log, delete_invalid, _controller):
            calls.append(("validate", path, delete_invalid))
            raise downloader.DownloadError("premiere_safe_mp4_validation_failed: invalid")

        with _patched_attr(downloader, "_run_ytdlp_with_retries", fake_download), _patched_attr(
            downloader,
            "_validate_premiere_safe_mp4_for_download",
            fake_validate,
        ), _patched_attr(downloader, "_premiere_safe_mp4_ready_for_download", lambda _path, _controller: False), _patched_attr(
            downloader,
            "_atomic_promote_with_retry",
            lambda *_args, **_kwargs: calls.append("promote"),
        ):
            try:
                downloader._download_video("video-1", "video", staging, final, _options(root), lambda _message: None)
            except downloader.DownloadError:
                pass
            else:
                raise AssertionError("invalid staged MP4 did not fail")

        _assert(calls[0] == "download", f"download was not first: {calls}")
        _assert(calls[1][0] == "validate", f"validation was not second: {calls}")
        _assert(calls[1][1].parent == staging, "validator was not called with staged path")
        _assert("promote" not in calls, "invalid staged MP4 was promoted")
        _assert(final.read_bytes() == b"OLD_MP4", "invalid staged MP4 replaced old final")

    with TemporaryDirectory(prefix="atomic_mp4_valid_") as temp_dir:
        root = Path(temp_dir)
        staging = root / "stage"
        staging.mkdir()
        final = root / "video.mp4"
        final.write_bytes(b"OLD_MP4")
        calls = []

        def fake_download(command, _options, _log, _controller=None, _state=None):
            calls.append("download")
            _output_path(command).write_bytes(b"GOOD_MP4")

        def fake_validate(path, _log, delete_invalid, _controller):
            calls.append(("validate", path, delete_invalid))

        def fake_promote(source, target, *_args, **_kwargs):
            calls.append(("promote", source, target))
            downloader.os.replace(source, target)

        with _patched_attr(downloader, "_run_ytdlp_with_retries", fake_download), _patched_attr(
            downloader,
            "_validate_premiere_safe_mp4_for_download",
            fake_validate,
        ), _patched_attr(downloader, "_premiere_safe_mp4_ready_for_download", lambda _path, _controller: False), _patched_attr(
            downloader,
            "_atomic_promote_with_retry",
            fake_promote,
        ):
            downloader._download_video("video-1", "video", staging, final, _options(root), lambda _message: None)

        _assert([item[0] if isinstance(item, tuple) else item for item in calls] == ["download", "validate", "promote"], calls)
        _assert(calls[1][1].parent == staging, "valid MP4 was not validated in staging")
        _assert(final.read_bytes() == b"GOOD_MP4", "valid staged MP4 was not promoted")


def _test_audio_promotion_failures_preserve_existing() -> None:
    with TemporaryDirectory(prefix="atomic_audio_direct_") as temp_dir:
        root = Path(temp_dir)
        staging = root / "stage"
        staging.mkdir()
        final = root / "audio.mp3"
        final.write_bytes(b"")

        def fake_download(command, _options, _log, _controller=None, _state=None):
            _output_path(command).write_bytes(b"NEW_MP3")

        with _patched_attr(downloader, "_run_ytdlp_with_retries", fake_download), _patched_attr(
            downloader,
            "_atomic_promote_with_retry",
            lambda source, target, *_args, **_kwargs: (_ for _ in ()).throw(
                downloader.FileOperationError("promote", source, target, PermissionError("locked"))
            ),
        ):
            try:
                downloader._download_audio("video-1", "audio", staging, final, _options(root), lambda _message: None)
            except downloader.FileOperationError:
                pass
            else:
                raise AssertionError("direct MP3 promotion failure did not propagate")
        _assert(final.read_bytes() == b"", "direct MP3 failure replaced old final")

    with TemporaryDirectory(prefix="atomic_audio_ffmpeg_") as temp_dir:
        root = Path(temp_dir)
        staging = root / "stage"
        staging.mkdir()
        source_video = root / "video.mp4"
        final = root / "audio.mp3"
        source_video.write_bytes(b"VIDEO")
        final.write_bytes(b"")

        def fake_ffmpeg(command, _controller=None):
            Path(command[-1]).write_bytes(b"NEW_MP3")
            return ""

        with _patched_attr(downloader, "_validate_premiere_safe_mp4_for_download", lambda *_args, **_kwargs: None), _patched_attr(
            downloader,
            "_run_ffmpeg_for_audio",
            fake_ffmpeg,
        ), _patched_attr(
            downloader,
            "_atomic_promote_with_retry",
            lambda source, target, *_args, **_kwargs: (_ for _ in ()).throw(
                downloader.FileOperationError("promote", source, target, PermissionError("locked"))
            ),
        ):
            try:
                downloader._extract_mp3_from_video(source_video, staging, final, lambda _message: None)
            except downloader.FileOperationError:
                pass
            else:
                raise AssertionError("FFmpeg MP3 promotion failure did not propagate")
        _assert(final.read_bytes() == b"", "FFmpeg MP3 failure replaced old final")


def _test_thumbnail_promotion_paths() -> None:
    with TemporaryDirectory(prefix="atomic_thumb_invalid_") as temp_dir:
        root = Path(temp_dir)
        final = root / "thumb.jpg"
        staged = root / "stage" / "thumb.jpg"
        staged.parent.mkdir()
        final.write_bytes(b"OLD_JPG")
        promoted = []
        with _patched_urlopen("image/jpeg", b"not-jpeg"), _patched_attr(
            downloader,
            "_atomic_promote_with_retry",
            lambda *_args, **_kwargs: promoted.append("promote"),
        ):
            try:
                downloader._download_thumbnail_from_url("https://example.invalid/thumb", staged, final)
            except downloader.DownloadError:
                pass
            else:
                raise AssertionError("invalid JPEG data was accepted")
        _assert(promoted == [], "invalid JPEG was promoted")
        _assert(final.read_bytes() == b"OLD_JPG", "invalid JPEG replaced old final")

    with TemporaryDirectory(prefix="atomic_thumb_fail_") as temp_dir:
        root = Path(temp_dir)
        final = root / "thumb.jpg"
        staged = root / "stage" / "thumb.jpg"
        staged.parent.mkdir()
        final.write_bytes(b"OLD_JPG")
        with _patched_urlopen("image/jpeg", b"\xff\xd8\xffNEW"), _patched_attr(
            downloader,
            "_atomic_promote_with_retry",
            lambda source, target, *_args, **_kwargs: (_ for _ in ()).throw(
                downloader.FileOperationError("promote", source, target, PermissionError("locked"))
            ),
        ):
            try:
                downloader._download_thumbnail_from_url("https://example.invalid/thumb", staged, final)
            except downloader.FileOperationError:
                pass
            else:
                raise AssertionError("JPEG promotion failure did not propagate")
        _assert(final.read_bytes() == b"OLD_JPG", "JPEG promotion failure replaced old final")

    with TemporaryDirectory(prefix="atomic_thumb_success_") as temp_dir:
        root = Path(temp_dir)
        final = root / "thumb.jpg"
        staged = root / "stage" / "thumb.jpg"
        staged.parent.mkdir()
        with _patched_urlopen("image/jpeg", b"\xff\xd8\xffNEW"):
            downloader._download_thumbnail_from_url("https://example.invalid/thumb", staged, final)
        _assert(final.read_bytes() == b"\xff\xd8\xffNEW", "valid JPEG was not promoted")


def _test_same_filesystem_enforced() -> None:
    with TemporaryDirectory(prefix="atomic_fs_") as temp_dir:
        root = Path(temp_dir)
        final = root / "final.mp4"
        staged = root / "stage" / "new.mp4"
        staged.parent.mkdir()
        final.write_bytes(b"OLD")
        staged.write_bytes(b"NEW")
        replace_calls = []
        move_calls = []
        with _patched_attr(downloader, "_paths_share_filesystem", lambda _source, _parent: False), _patched_attr(
            downloader.os,
            "replace",
            lambda *_args: replace_calls.append("replace"),
        ), _patched_attr(
            downloader.shutil,
            "move",
            lambda *_args, **_kwargs: move_calls.append("move"),
        ):
            try:
                downloader._atomic_promote_with_retry(staged, final, replace_existing=True)
            except downloader.FileOperationError:
                pass
            else:
                raise AssertionError("cross-filesystem promotion did not fail")
        _assert(replace_calls == [], "os.replace was called after filesystem mismatch")
        _assert(move_calls == [], "shutil.move fallback was used")
        _assert(final.read_bytes() == b"OLD", "filesystem mismatch changed final")


def _test_no_destination_unlink_or_move_fallback() -> None:
    source = Path("core") / "downloader.py"
    text = source.read_text(encoding="utf-8")
    helper_source = inspect.getsource(downloader._atomic_promote_with_retry)
    _assert("final_path.unlink" not in text, "final_path.unlink remains in downloader")
    _assert("shutil.move" not in text, "shutil.move remains in downloader")
    _assert("os.replace" in helper_source, "atomic helper does not use os.replace")


def _test_state_update_follows_part_success() -> None:
    with TemporaryDirectory(prefix="atomic_state_success_") as temp_dir:
        calls = _run_single_video_batch(Path(temp_dir), download_raises=False)
        _assert(calls[:2] == ["promote", ("state", STATUS_DOWNLOADED)], f"state did not follow promotion: {calls}")

    with TemporaryDirectory(prefix="atomic_state_fail_") as temp_dir:
        calls = _run_single_video_batch(Path(temp_dir), download_raises=True)
        _assert(("state", STATUS_DOWNLOADED) not in calls, f"downloaded state was written after failed promotion: {calls}")
        _assert(("state", STATUS_ERROR) in calls, "failed attempt was not marked error")


def _test_staging_cleanup_all_terminal_paths() -> None:
    outcomes = ("success", "validation", "promotion", "cancel", "unexpected")
    for outcome in outcomes:
        with TemporaryDirectory(prefix=f"atomic_cleanup_{outcome}_") as temp_dir:
            channel_dir = Path(temp_dir) / "Channel"
            final = channel_dir / "video" / "final.mp4"
            final.parent.mkdir(parents=True)
            if outcome == "success":
                with downloader._media_staging_directory(channel_dir, "video", lambda _message: None) as stage:
                    staged = stage / "new.mp4"
                    staged.write_bytes(b"NEW")
                    downloader._atomic_promote_with_retry(staged, final, replace_existing=True)
                    stage_path = stage
                _assert(final.read_bytes() == b"NEW", "promoted final did not survive cleanup")
            else:
                try:
                    with downloader._media_staging_directory(channel_dir, "video", lambda _message: None) as stage:
                        stage_path = stage
                        (stage / "attempt.tmp").write_bytes(b"x")
                        if outcome == "validation":
                            raise downloader.DownloadError("validation failed")
                        if outcome == "promotion":
                            raise downloader.FileOperationError("promote", stage / "a", final, PermissionError("x"))
                        if outcome == "cancel":
                            raise downloader.DownloadCancelled("download cancelled/interrupted")
                        raise RuntimeError("unexpected")
                except (downloader.DownloadError, RuntimeError):
                    pass
            _assert(not stage_path.exists(), f"staging not cleaned after {outcome}")


def _run_single_video_batch(root: Path, download_raises: bool) -> list:
    calls = []
    video = SimpleNamespace(
        video_id="state-video",
        title="State Video",
        sanitized_filename_base="state-video",
        display_order=1,
        thumbnail_url="",
        status="",
    )
    options = DownloadOptions(str(root), "channel", "Channel", download_mode=MODE_VIDEO_THUMB)
    originals = {
        "validate_download_environment": downloader.validate_download_environment,
        "_call_runtime_tool_summary": downloader._call_runtime_tool_summary,
        "get_video_entry": downloader.get_video_entry,
        "is_mode_complete": downloader.is_mode_complete,
        "missing_parts_for_mode": downloader.missing_parts_for_mode,
        "_download_video": downloader._download_video,
        "update_video_part_state": downloader.update_video_part_state,
        "_reconcile_current_item": downloader._reconcile_current_item,
    }

    def fake_download(*_args, **_kwargs):
        calls.append("promote")
        if download_raises:
            raise downloader.FileOperationError("promote", Path("stage"), Path("final"), PermissionError("locked"))

    def fake_update(*args, **_kwargs):
        calls.append(("state", args[6]))

    try:
        downloader.validate_download_environment = lambda _options: None
        downloader._call_runtime_tool_summary = lambda *_args, **_kwargs: None
        downloader.get_video_entry = lambda *_args, **_kwargs: {}
        downloader.is_mode_complete = lambda *_args, **_kwargs: False
        downloader.missing_parts_for_mode = lambda *_args, **_kwargs: [PART_VIDEO]
        downloader._download_video = fake_download
        downloader.update_video_part_state = fake_update
        downloader._reconcile_current_item = lambda *_args, **_kwargs: STATUS_DOWNLOADED
        downloader.download_items([video], options, lambda _message: None, lambda _video: None)
    finally:
        for name, value in originals.items():
            setattr(downloader, name, value)
    return calls


def _output_path(command: list[str]) -> Path:
    template = Path(command[command.index("-o") + 1])
    if "%(ext)s" not in template.name:
        return template
    extension = "mp3" if "--audio-format" in command else "mp4"
    return template.with_name(template.name.replace("%(ext)s", extension))


def _options(root: Path) -> DownloadOptions:
    return DownloadOptions(str(root), "channel", "Channel", download_mode=MODE_VIDEO_THUMB)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


class _patched_attr:
    def __init__(self, obj, name: str, replacement):
        self.obj = obj
        self.name = name
        self.replacement = replacement
        self.original = None

    def __enter__(self):
        self.original = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.replacement)
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        setattr(self.obj, self.name, self.original)


class _FakeResponse:
    def __init__(self, content_type: str, data: bytes):
        self.headers = {"Content-Type": content_type}
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def read(self) -> bytes:
        return self.data


class _patched_urlopen:
    def __init__(self, content_type: str, data: bytes):
        self.content_type = content_type
        self.data = data
        self.original = None

    def __enter__(self):
        self.original = downloader.urllib.request.urlopen
        downloader.urllib.request.urlopen = lambda *_args, **_kwargs: _FakeResponse(self.content_type, self.data)
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        downloader.urllib.request.urlopen = self.original


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
