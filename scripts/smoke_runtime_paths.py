import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import runtime_paths


def main() -> int:
    _test_primary_runtime_wins()
    _test_app_root_runtime_compatibility()
    _test_legacy_runtime_is_disabled_by_default()
    _test_legacy_runtime_requires_exact_opt_in()
    _test_frozen_runtime_never_uses_legacy()
    _test_missing_runtime_returns_primary_path()
    print("runtime paths smoke tests passed")
    return 0


def _test_primary_runtime_wins() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "app"
        legacy = Path(temp_dir) / "legacy"
        primary = root / "data" / "bin" / "yt-dlp.exe"
        primary.parent.mkdir(parents=True)
        primary.write_bytes(b"primary")
        legacy.mkdir()
        (legacy / "yt-dlp.exe").write_bytes(b"legacy")
        with _runtime_environment(root, legacy, frozen=False, setting="1"):
            _assert(runtime_paths.runtime_file("yt-dlp.exe") == primary, "legacy runtime overrode primary")


def _test_app_root_runtime_compatibility() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "app"
        legacy = Path(temp_dir) / "legacy"
        root.mkdir()
        app_root_runtime = root / "yt-dlp.exe"
        app_root_runtime.write_bytes(b"compatible")
        with _runtime_environment(root, legacy, frozen=False, setting=None):
            _assert(runtime_paths.runtime_file("yt-dlp.exe") == app_root_runtime, "app-root compatibility was lost")


def _test_legacy_runtime_is_disabled_by_default() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "app"
        legacy = Path(temp_dir) / "legacy"
        legacy.mkdir()
        (legacy / "ffmpeg.exe").write_bytes(b"legacy")
        with _runtime_environment(root, legacy, frozen=False, setting=None):
            expected = root / "data" / "bin" / "ffmpeg.exe"
            _assert(runtime_paths.runtime_file("ffmpeg.exe") == expected, "legacy runtime was enabled by default")


def _test_legacy_runtime_requires_exact_opt_in() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "app"
        legacy = Path(temp_dir) / "legacy"
        legacy.mkdir()
        legacy_runtime = legacy / "aria2c.exe"
        legacy_runtime.write_bytes(b"legacy")

        with _runtime_environment(root, legacy, frozen=False, setting="1"):
            _assert(runtime_paths.runtime_file("aria2c.exe") == legacy_runtime, "exact legacy opt-in was ignored")

        primary = root / "data" / "bin" / "aria2c.exe"
        for rejected in ("", "true", "yes", "on"):
            with _runtime_environment(root, legacy, frozen=False, setting=rejected):
                _assert(runtime_paths.runtime_file("aria2c.exe") == primary, f"legacy opt-in accepted {rejected!r}")


def _test_frozen_runtime_never_uses_legacy() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "frozen-app"
        legacy = Path(temp_dir) / "legacy"
        legacy.mkdir()
        (legacy / "deno.exe").write_bytes(b"legacy")
        with _runtime_environment(root, legacy, frozen=True, setting="1"):
            expected = root / "data" / "bin" / "deno.exe"
            _assert(runtime_paths.runtime_file("deno.exe") == expected, "frozen runtime used legacy fallback")


def _test_missing_runtime_returns_primary_path() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "app"
        legacy = Path(temp_dir) / "legacy"
        with _runtime_environment(root, legacy, frozen=False, setting="1"):
            expected = root / "data" / "bin" / "yt-dlp.exe"
            _assert(runtime_paths.runtime_file("yt-dlp.exe") == expected, "missing runtime did not return primary path")


@contextmanager
def _runtime_environment(root: Path, legacy: Path, *, frozen: bool, setting: str | None):
    original_app_root = runtime_paths.app_root
    original_is_frozen = runtime_paths.is_frozen
    original_legacy = runtime_paths.LEGACY_RUNTIME_DIR
    original_setting = os.environ.get("S9H_ALLOW_LEGACY_RUNTIME")
    try:
        runtime_paths.app_root = lambda: root
        runtime_paths.is_frozen = lambda: frozen
        runtime_paths.LEGACY_RUNTIME_DIR = legacy
        if setting is None:
            os.environ.pop("S9H_ALLOW_LEGACY_RUNTIME", None)
        else:
            os.environ["S9H_ALLOW_LEGACY_RUNTIME"] = setting
        yield
    finally:
        runtime_paths.app_root = original_app_root
        runtime_paths.is_frozen = original_is_frozen
        runtime_paths.LEGACY_RUNTIME_DIR = original_legacy
        if original_setting is None:
            os.environ.pop("S9H_ALLOW_LEGACY_RUNTIME", None)
        else:
            os.environ["S9H_ALLOW_LEGACY_RUNTIME"] = original_setting


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
