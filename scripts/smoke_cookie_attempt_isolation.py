import sys
import re
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.downloader import COOKIE_SOURCE_BRIDGE, DownloadError, DownloadOptions


def main() -> int:
    _test_temp_cookie_isolation()
    _test_stable_copy_retry_when_canonical_changes()
    _test_cookie_snapshot_logs_do_not_include_hash()
    print("cookie attempt isolation smoke passed")
    return 0


def _test_temp_cookie_isolation() -> None:
    with TemporaryDirectory(prefix="cookie_isolation_smoke_") as temp_dir:
        root = Path(temp_dir)
        canonical = root / "youtube_cookies.txt"
        original = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\told\n"
        canonical.write_text(original, encoding="utf-8")
        options = _options(canonical)
        command = [
            "yt-dlp",
            downloader.YTDLP_COOKIES_OPTION,
            str(canonical),
            "--newline",
            "https://www.youtube.com/watch?v=abc123",
        ]

        with downloader._prepared_cookie_attempt(command, options, lambda _message: None) as attempt:
            cookie_path = Path(downloader._command_cookie_path(attempt.command))
            _assert(cookie_path != canonical, "yt-dlp attempt used canonical cookie path")
            _assert(str(canonical) not in attempt.command, "canonical cookie path remained in yt-dlp command")
            _assert(cookie_path.exists(), "temp cookie copy was missing during attempt")
            _assert(cookie_path.read_text(encoding="utf-8") == original, "temp cookie content did not match canonical")
            cookie_path.write_text("yt-dlp writeback\n", encoding="utf-8")
            _assert(canonical.read_text(encoding="utf-8") == original, "temp writeback mutated canonical cookie file")
            temp_cookie_path = cookie_path

        _assert(not temp_cookie_path.exists(), "temp cookie copy was not cleaned up")


def _test_stable_copy_retry_when_canonical_changes() -> None:
    with TemporaryDirectory(prefix="cookie_stable_copy_smoke_") as temp_dir:
        root = Path(temp_dir)
        canonical = root / "youtube_cookies.txt"
        canonical.write_text("cookie=v1\n", encoding="utf-8")
        options = _options(canonical)
        command = ["yt-dlp", "https://www.youtube.com/watch?v=stable123"]

        old_copy_cookie_file = downloader._copy_cookie_file
        calls = {"copy": 0}
        try:
            def copy_and_mutate(source: Path, target: Path) -> None:
                calls["copy"] += 1
                old_copy_cookie_file(source, target)
                if calls["copy"] == 1:
                    source.write_text("cookie=v2\n", encoding="utf-8")

            downloader._copy_cookie_file = copy_and_mutate
            with downloader._prepared_cookie_attempt(command, options, lambda _message: None) as attempt:
                cookie_path = Path(downloader._command_cookie_path(attempt.command))
                _assert(cookie_path.read_text(encoding="utf-8") == "cookie=v2\n", "stable retry did not use latest canonical content")
        finally:
            downloader._copy_cookie_file = old_copy_cookie_file

        _assert(calls["copy"] == 2, f"stable copy retry count was wrong: {calls['copy']}")


def _test_cookie_snapshot_logs_do_not_include_hash() -> None:
    with TemporaryDirectory(prefix="cookie_log_smoke_") as temp_dir:
        root = Path(temp_dir)
        canonical = root / "youtube_cookies.txt"
        canonical.write_text("cookie=v1\n", encoding="utf-8")
        options = _options(canonical)
        logs = []
        old_copy_cookie_file = downloader._copy_cookie_file
        calls = {"copy": 0}
        try:
            def copy_and_mutate(source: Path, target: Path) -> None:
                calls["copy"] += 1
                old_copy_cookie_file(source, target)
                source.write_text(f"cookie=v{calls['copy'] + 1}\n", encoding="utf-8")

            downloader._copy_cookie_file = copy_and_mutate
            try:
                with downloader._prepared_cookie_attempt(
                    ["yt-dlp", "https://www.youtube.com/watch?v=hash123"],
                    options,
                    logs.append,
                ):
                    raise AssertionError("unstable cookie copy unexpectedly succeeded")
            except DownloadError:
                pass
        finally:
            downloader._copy_cookie_file = old_copy_cookie_file

        joined_logs = "\n".join(logs)
        _assert(not re.search(r"sha256\s*=", joined_logs, re.IGNORECASE), "cookie logs included sha256 text")
        _assert("snapshot_available=" in joined_logs, "cookie logs did not include safe snapshot availability")


def _options(cookie_path: Path) -> DownloadOptions:
    return DownloadOptions(
        base_folder=".",
        channel_id="channel",
        channel_name="Channel",
        cookies_enabled=True,
        cookie_source=COOKIE_SOURCE_BRIDGE,
        bridge_cookie_path=str(cookie_path),
    )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
