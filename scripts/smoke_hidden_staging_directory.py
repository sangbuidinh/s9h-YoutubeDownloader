import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader


@contextmanager
def _patched_attr(obj, name, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def _windows_hidden(path: Path) -> bool:
    if os.name != "nt":
        return False

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = [ctypes.c_wchar_p]
    get_attributes.restype = ctypes.c_uint32
    attributes = get_attributes(str(path))
    _assert(attributes != 0xFFFFFFFF, "GetFileAttributesW failed for staging directory")
    return bool(attributes & 0x00000002)


def main() -> int:
    _test_existing_staging_directories_are_marked_hidden()
    _test_staging_creation_invokes_hidden_marker()
    _test_real_windows_hidden_attribute()
    print("hidden staging directory smoke passed")
    return 0


def _test_existing_staging_directories_are_marked_hidden() -> None:
    calls = []

    def record_hidden(path, log=None):
        calls.append(Path(path))

    with TemporaryDirectory(prefix="hidden_stage_existing_") as temp_dir:
        channel_dir = Path(temp_dir) / "Channel"
        channel_dir.mkdir()
        old_stage = channel_dir / ".s9h-stage-old-video-abc"
        old_stage.mkdir()
        normal_dir = channel_dir / "video"
        normal_dir.mkdir()
        with _patched_attr(downloader, "_mark_staging_directory_hidden", record_hidden):
            downloader._hide_existing_staging_directories(channel_dir)

        _assert(old_stage in calls, "older staging directory was not marked hidden")
        _assert(normal_dir not in calls, "normal output directory was marked hidden")


def _test_staging_creation_invokes_hidden_marker() -> None:
    calls = []

    def record_hidden(path, log=None):
        calls.append((Path(path), log))

    with TemporaryDirectory(prefix="hidden_stage_call_") as temp_dir:
        channel_dir = Path(temp_dir) / "Channel"
        log = lambda _message: None
        with _patched_attr(downloader, "_mark_staging_directory_hidden", record_hidden):
            with downloader._media_staging_directory(channel_dir, "video-1", log) as staging_path:
                _assert(staging_path.exists(), "staging directory was not created")
                _assert(len(calls) == 1, "hidden marker was not called exactly once")
                _assert(calls[0][0] == staging_path, "hidden marker received the wrong path")
                _assert(calls[0][1] is log, "hidden marker did not receive the active logger")
            _assert(not staging_path.exists(), "staging directory was not cleaned")


def _test_real_windows_hidden_attribute() -> None:
    if os.name != "nt":
        return

    with TemporaryDirectory(prefix="hidden_stage_native_") as temp_dir:
        channel_dir = Path(temp_dir) / "Channel"
        with downloader._media_staging_directory(channel_dir, "video-2") as staging_path:
            _assert(_windows_hidden(staging_path), "staging directory is not hidden on Windows")


if __name__ == "__main__":
    raise SystemExit(main())
