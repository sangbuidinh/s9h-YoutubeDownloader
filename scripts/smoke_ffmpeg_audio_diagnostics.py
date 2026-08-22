import io
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.download_modes import MODE_VIDEO_AUDIO_THUMB, PART_AUDIO, PART_VIDEO
from core.error_messages import friendly_ffmpeg_failure_kind_error
from core.file_status import STATUS_DOWNLOADED, STATUS_ERROR


def main() -> int:
    _test_structured_non_zero_exit()
    _test_long_stdout_cannot_displace_stderr()
    _test_disk_full_stderr_outranks_long_stdout()
    _test_stdout_fallback()
    _test_stdout_fallback_after_stderr_noise()
    _test_classification_table()
    _test_output_bounds()
    _test_sanitization()
    _test_extensionless_absolute_path_sanitization()
    _test_quoted_absolute_paths_with_spaces()
    _test_url_preservation()
    _test_url_aware_secret_redaction()
    _test_standalone_colon_secret_redaction()
    _test_url_and_secret_regression()
    _test_exception_fields_safe()
    _test_banner_and_progress_filtering()
    _test_cancellation()
    _test_process_not_found()
    _test_process_creation_os_error()
    _test_outer_loop_friendly_log()
    _test_zero_byte_output_after_exit_zero()
    _test_success_promotes_mp3()
    print("ffmpeg audio diagnostics smoke tests passed")
    return 0


class _FakeProcess:
    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = returncode
        self.pid = 4242

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class _TimeoutProcess:
    def __init__(self, on_timeout):
        self.stdout = _CancellingStream(lambda: on_timeout(self))
        self.stderr = io.StringIO("")
        self.returncode = None
        self.pid = 4243

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("ffmpeg.exe", timeout)
        return self.returncode

    def poll(self):
        return self.returncode


class _CancellingStream:
    def __init__(self, on_read):
        self._on_read = on_read
        self._used = False

    def __iter__(self):
        if not self._used:
            self._used = True
            self._on_read()
        return iter(())


@contextmanager
def _patched(obj, name: str, value):
    old_value = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old_value)


@contextmanager
def _patched_many(patches):
    old_values = []
    try:
        for obj, name, value in patches:
            old_values.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, old_value in reversed(old_values):
            setattr(obj, name, old_value)


def _run_audio_with_process(process):
    with _patched(downloader.subprocess, "Popen", lambda *args, **kwargs: process):
        return downloader._run_ffmpeg_for_audio(["ffmpeg.exe", "-i", "input.mp4", "output.mp3"])


def _capture_ffmpeg_error(process) -> downloader.FFmpegExecutionError:
    try:
        _run_audio_with_process(process)
    except downloader.FFmpegExecutionError as exc:
        return exc
    raise AssertionError("expected FFmpegExecutionError")


def _test_structured_non_zero_exit() -> None:
    exc = _capture_ffmpeg_error(
        _FakeProcess(stderr="Invalid data found when processing input", returncode=1)
    )
    _assert(exc.operation == "extract_mp3", f"wrong operation: {exc.operation}")
    _assert(exc.exit_code == 1, f"wrong exit code: {exc.exit_code}")
    _assert("Invalid data found when processing input" in "\n".join(exc.output_lines), "stderr reason missing")
    _assert(
        downloader.classify_ffmpeg_failure_kind(exc) == downloader.FFmpegFailureKind.INVALID_INPUT,
        "invalid input was not classified",
    )
    _assert(str(exc) != "audio extraction failed", "non-zero exit used the old plain error")


def _test_long_stdout_cannot_displace_stderr() -> None:
    stdout = "\n".join(f"ordinary stdout diagnostic {index}" for index in range(1000))
    exc = _capture_ffmpeg_error(
        _FakeProcess(
            stdout=stdout,
            stderr="Invalid data found when processing input",
            returncode=1,
        )
    )
    joined = "\n".join(exc.output_lines)
    _assert("Invalid data found when processing input" in joined, "stderr marker missing from output_lines")
    _assert(
        "Invalid data found when processing input" in exc.combined_output,
        "stderr marker missing from combined_output",
    )
    _assert("ordinary stdout diagnostic" not in joined, "stdout displaced meaningful stderr")
    _assert(len(exc.output_lines) <= downloader.FFMPEG_OUTPUT_LINE_LIMIT, "output line limit changed")
    _assert(
        len(exc.combined_output) <= downloader.FFMPEG_COMBINED_OUTPUT_LIMIT,
        "combined output limit changed",
    )
    _assert(
        downloader.classify_ffmpeg_failure_kind(exc) == downloader.FFmpegFailureKind.INVALID_INPUT,
        "classification did not use preserved stderr evidence",
    )


def _test_disk_full_stderr_outranks_long_stdout() -> None:
    stdout = "\n".join("Conversion failed" for _index in range(500))
    exc = _capture_ffmpeg_error(
        _FakeProcess(stdout=stdout, stderr="No space left on device", returncode=28)
    )
    _assert("No space left on device" in "\n".join(exc.output_lines), "disk-full stderr was not retained")
    _assert(
        downloader.classify_ffmpeg_failure_kind(exc) == downloader.FFmpegFailureKind.DISK_FULL,
        "disk-full stderr did not outrank long stdout",
    )


def _test_stdout_fallback() -> None:
    exc = _capture_ffmpeg_error(
        _FakeProcess(stdout="Unknown encoder 'libmp3lame'", stderr="", returncode=8)
    )
    _assert(exc.exit_code == 8, f"wrong stdout-fallback exit code: {exc.exit_code}")
    _assert("Unknown encoder 'libmp3lame'" in "\n".join(exc.output_lines), "stdout reason missing")
    _assert(
        downloader.classify_ffmpeg_failure_kind(exc) == downloader.FFmpegFailureKind.ENCODER_UNAVAILABLE,
        "encoder failure was not classified",
    )


def _test_stdout_fallback_after_stderr_noise() -> None:
    stderr_noise = "\n".join(
        [
            "ffmpeg version 7.0 Copyright (c)",
            "built with gcc 13",
            "configuration: --enable-gpl --enable-libmp3lame",
            "libavutil      59.  8.100 / 59.  8.100",
            "frame=  101 fps=0.0 q=0.0 size=128kB time=00:00:04.00 bitrate=256.0kbits/s speed=8x",
        ]
    )
    _assert(
        downloader._collect_meaningful_ffmpeg_lines(stderr_noise) == [],
        "stderr noise should not be meaningful",
    )
    exc = _capture_ffmpeg_error(
        _FakeProcess(stdout="Unknown encoder 'libmp3lame'", stderr=stderr_noise, returncode=8)
    )
    _assert("Unknown encoder 'libmp3lame'" in "\n".join(exc.output_lines), "stdout fallback missing")
    _assert(
        downloader.classify_ffmpeg_failure_kind(exc) == downloader.FFmpegFailureKind.ENCODER_UNAVAILABLE,
        "stdout fallback was not classified",
    )


def _test_classification_table() -> None:
    cases = {
        "No space left on device": downloader.FFmpegFailureKind.DISK_FULL,
        "Permission denied": downloader.FFmpegFailureKind.PERMISSION_DENIED,
        "Unknown encoder 'libmp3lame'": downloader.FFmpegFailureKind.ENCODER_UNAVAILABLE,
        "Output file does not contain any stream": downloader.FFmpegFailureKind.NO_AUDIO_STREAM,
        "Could not open output file": downloader.FFmpegFailureKind.OUTPUT_PATH,
        "moov atom not found": downloader.FFmpegFailureKind.INVALID_INPUT,
        "Broken pipe": downloader.FFmpegFailureKind.INTERRUPTED_WRITE,
        "unfamiliar text": downloader.FFmpegFailureKind.UNKNOWN,
    }
    for output, expected in cases.items():
        actual = downloader.classify_ffmpeg_failure_kind(_ffmpeg_exc(output))
        _assert(actual == expected, f"{output!r} classified as {actual}, expected {expected}")

    mixed = _ffmpeg_exc("Conversion failed\nNo space left on device")
    _assert(
        downloader.classify_ffmpeg_failure_kind(mixed) == downloader.FFmpegFailureKind.DISK_FULL,
        "disk-full precedence was not preserved",
    )


def _test_output_bounds() -> None:
    stderr = "\n".join([f"diagnostic line {index}" for index in range(250)])
    stderr += "\nNo space left on device"
    exc = _capture_ffmpeg_error(_FakeProcess(stderr=stderr, returncode=99))
    _assert(len(exc.output_lines) <= downloader.FFMPEG_OUTPUT_LINE_LIMIT, "line limit was not enforced")
    _assert(
        len(exc.combined_output) <= downloader.FFMPEG_COMBINED_OUTPUT_LIMIT,
        "combined output limit was not enforced",
    )
    _assert("No space left on device" in "\n".join(exc.output_lines), "final relevant line was not retained")


def _test_sanitization() -> None:
    api_key = "AI" "za" + ("X" * 32)
    lines = [
        r"D:\Youtube Downloader Source\data\.s9h-stage\video.mp4",
        r"C:\Users\Alice\AppData\Local\Temp\output.mp3",
        "/home/alice/private/input.mp4",
        "https://example.test/path?key=SECRET_VALUE&token=SECRET_TOKEN",
        api_key,
        "Cookie: SID=secret",
        r"--cookies C:\Users\Alice\cookies.txt",
        "Invalid data found when processing input",
    ]
    sanitized = "\n".join(downloader._sanitize_subprocess_output_line(line) for line in lines)
    forbidden = [
        r"D:\Youtube Downloader Source",
        r"C:\Users\Alice",
        "/home/alice",
        "Alice",
        "SECRET_VALUE",
        "SECRET_TOKEN",
        "AI" "za",
        "SID=secret",
        "Cookie:",
        "--cookies",
    ]
    for value in forbidden:
        _assert(value not in sanitized, f"unsanitized value leaked: {value}")
    for value in ("video.mp4", "output.mp3", "input.mp4", "Invalid data found when processing input"):
        _assert(value in sanitized, f"useful sanitized value missing: {value}")


def _test_extensionless_absolute_path_sanitization() -> None:
    cases = [
        (
            r"Error opening output D:\Secret\User\NoExtensionDir: No such file or directory",
            downloader.FFmpegFailureKind.OUTPUT_PATH,
            (r"D:\Secret\User", "Secret", "User"),
            ("NoExtensionDir", "No such file or directory"),
        ),
        (
            "Could not open C:/Users/Alice/Private Folder/outputdir: Permission denied",
            downloader.FFmpegFailureKind.PERMISSION_DENIED,
            ("C:/Users/Alice", "Alice", "Private Folder"),
            ("outputdir", "Permission denied"),
        ),
        (
            "Could not open /home/alice/private/outputdir: Permission denied",
            downloader.FFmpegFailureKind.PERMISSION_DENIED,
            ("/home/alice/private", "alice"),
            ("outputdir", "Permission denied"),
        ),
        (
            r"Could not open \\server\private-share\secret-folder: Access is denied",
            downloader.FFmpegFailureKind.PERMISSION_DENIED,
            ("server", "private-share"),
            ("secret-folder", "Access is denied"),
        ),
    ]
    for raw, expected_kind, forbidden, required in cases:
        sanitized = downloader._sanitize_subprocess_output_line(raw)
        for value in forbidden:
            _assert(value not in sanitized, f"raw path component leaked from {raw!r}: {sanitized!r}")
        for value in required:
            _assert(value in sanitized, f"expected diagnostic text missing from {raw!r}: {sanitized!r}")
        _assert(
            downloader.classify_ffmpeg_failure_kind(_ffmpeg_exc(sanitized)) == expected_kind,
            f"sanitized path diagnostic classified incorrectly: {sanitized!r}",
        )


def _test_quoted_absolute_paths_with_spaces() -> None:
    cases = [
        (
            r'Error opening output "D:\Secret User\Stage Folder\Final Output": Invalid argument',
            '"',
            ("Secret User", "Stage Folder"),
            ("Final Output", "Invalid argument"),
        ),
        (
            "Error opening output '/home/alice/private folder/final output': Invalid argument",
            "'",
            ("alice", "private folder"),
            ("final output", "Invalid argument"),
        ),
    ]
    for raw, quote, forbidden, required in cases:
        sanitized = downloader._sanitize_subprocess_output_line(raw)
        _assert(sanitized.count(quote) == 2, f"quotes were not balanced: {sanitized!r}")
        for value in forbidden:
            _assert(value not in sanitized, f"quoted path parent leaked: {sanitized!r}")
        for value in required:
            _assert(value in sanitized, f"quoted path useful text missing: {sanitized!r}")


def _test_url_preservation() -> None:
    unchanged = [
        "https://example.test/path",
        "See https://example.test/path for details",
        "http://example.test/path",
        "https://example.test/path: connection failed",
        "https://example.test:8443/path",
        "https://[::1]/path",
        "https://example.test/path?mode=fast",
        "https://example.test/path#section",
        "URL https://example.test/path,",
        "URL https://example.test/path;",
        "URL (https://example.test/path)",
        "URL [https://example.test/path]",
        "Primary https://one.test/a fallback http://two.test/b",
        "file:///C:/Temp/output.mp3",
    ]
    for raw in unchanged:
        sanitized = downloader._sanitize_subprocess_output_line(raw)
        _assert(sanitized == raw, f"URL was corrupted: {raw!r} -> {sanitized!r}")
        _assert("<path>" not in sanitized, f"URL was converted to a path placeholder: {sanitized!r}")
        _assert("__S9H_PROTECTED_URL_" not in sanitized, f"URL placeholder leaked: {sanitized!r}")

    mixed_windows = downloader._sanitize_subprocess_output_line(
        r"See https://example.test/help for D:\Secret\User\NoExtensionDir"
    )
    _assert("https://example.test/help" in mixed_windows, f"URL was not preserved: {mixed_windows!r}")
    _assert(r"D:\Secret\User" not in mixed_windows, f"Windows path parent leaked: {mixed_windows!r}")
    _assert("NoExtensionDir" in mixed_windows, f"Windows basename missing: {mixed_windows!r}")

    mixed_posix = downloader._sanitize_subprocess_output_line(
        "Docs: https://example.test/help; file: /home/alice/private/output.mp3"
    )
    _assert("https://example.test/help" in mixed_posix, f"URL was not preserved: {mixed_posix!r}")
    _assert("/home/alice/private" not in mixed_posix, f"POSIX path parent leaked: {mixed_posix!r}")
    _assert("alice" not in mixed_posix, f"POSIX username leaked: {mixed_posix!r}")
    _assert("output.mp3" in mixed_posix, f"POSIX basename missing: {mixed_posix!r}")


def _test_url_aware_secret_redaction() -> None:
    exact_cases = {
        "https://example.test/path?key=SECRET_VALUE#section": "https://example.test/path?key=***#section",
        "https://example.test/path?mode=fast&key=SECRET#section": "https://example.test/path?mode=fast&key=***#section",
        "https://example.test/path?key=SECRET&mode=fast#section": "https://example.test/path?key=***&mode=fast#section",
        "https://example.test/path?token=SECRET#one#two": "https://example.test/path?token=***#one#two",
        "URL (https://example.test/path?key=SECRET)": "URL (https://example.test/path?key=***)",
        "URL [https://example.test/path?key=SECRET]": "URL [https://example.test/path?key=***]",
        "URL https://example.test/path?key=SECRET, next": "URL https://example.test/path?key=***, next",
        "URL https://example.test/path?key=SECRET; next": "URL https://example.test/path?key=***; next",
        "URL https://example.test/path?key=SECRET: connection failed": "URL https://example.test/path?key=***: connection failed",
        "https://example.test:8443/path?key=SECRET": "https://example.test:8443/path?key=***",
        "https://[::1]/path?token=SECRET#fragment": "https://[::1]/path?token=***#fragment",
        "https://example.test/path?key=A&token=B&mode=fast&api_key=C": "https://example.test/path?key=***&token=***&mode=fast&api_key=***",
        "https://example.test/path?key=A&key=B": "https://example.test/path?key=***&key=***",
        "https://example.test/path?KEY=A&Api_Key=B&TOKEN=C&Access_Token=D&mode=fast": "https://example.test/path?KEY=***&Api_Key=***&TOKEN=***&Access_Token=***&mode=fast",
        "https://example.test/path?key=": "https://example.test/path?key=***",
        "https://example.test/path?key=&mode=fast": "https://example.test/path?key=***&mode=fast",
        "https://example.test/path?key=SECRET%20VALUE": "https://example.test/path?key=***",
        "https://example.test/path?key=SECRET+VALUE": "https://example.test/path?key=***",
        "https://example.test/path?mode=fast": "https://example.test/path?mode=fast",
        "custom+scheme://host/path?token=SECRET": "custom+scheme://host/path?token=***",
        "https://": "https://",
        "https://example.test/?": "https://example.test/?",
        "https://example.test/?key": "https://example.test/?key",
        "https://example.test/?key=": "https://example.test/?key=***",
        "https://example.test/?=value": "https://example.test/?=value",
    }
    for raw, expected in exact_cases.items():
        sanitized = downloader._sanitize_subprocess_output_line(raw)
        _assert(sanitized == expected, f"URL-aware redaction mismatch: {raw!r} -> {sanitized!r}")
        _assert("SECRET" not in sanitized and "VALUE" not in sanitized, f"secret leaked: {sanitized!r}")
        _assert("__S9H_PROTECTED_URL_" not in sanitized, f"placeholder leaked: {sanitized!r}")

    assignment_cases = {
        "token=SECRET, retrying": "token=***, retrying",
        "key=SECRET: request failed": "key=***: request failed",
        "(api_key=SECRET)": "(api_key=***)",
        "access_token=SECRET]": "access_token=***]",
    }
    for raw, expected in assignment_cases.items():
        sanitized = downloader._sanitize_subprocess_output_line(raw)
        _assert(sanitized == expected, f"standalone assignment mismatch: {raw!r} -> {sanitized!r}")

    mixed_windows = downloader._sanitize_subprocess_output_line(
        r"Docs https://example.test/help?key=SECRET; file D:\Secret\User\NoExtensionDir"
    )
    _assert(
        "Docs https://example.test/help?key=***; file " in mixed_windows,
        f"URL secret or semicolon was not preserved: {mixed_windows!r}",
    )
    _assert(r"D:\Secret\User" not in mixed_windows and "NoExtensionDir" in mixed_windows, mixed_windows)

    mixed_posix = downloader._sanitize_subprocess_output_line(
        "Docs https://example.test/help?token=SECRET; file /home/alice/private/output.mp3"
    )
    _assert("https://example.test/help?token=***" in mixed_posix, mixed_posix)
    _assert("/home/alice/private" not in mixed_posix and "alice" not in mixed_posix, mixed_posix)
    _assert("output.mp3" in mixed_posix, mixed_posix)

    multiple = downloader._sanitize_subprocess_output_line(
        "Primary https://one.test/a?key=ONE fallback http://two.test/b?token=TWO#frag"
    )
    _assert(
        multiple == "Primary https://one.test/a?key=*** fallback http://two.test/b?token=***#frag",
        f"multiple URL redaction mismatch: {multiple!r}",
    )


def _test_standalone_colon_secret_redaction() -> None:
    exact_cases = {
        "token=abc:def": "token=***",
        "key=part1:part2": "key=***",
        "token=a:b:c": "token=***",
        "access_token=part1:part2:part3": "access_token=***",
        "token=abc:def: request failed": "token=***: request failed",
        "key=part1:part2: authentication failed": "key=***: authentication failed",
        "token=host:8443": "token=***",
        "token=[::1]:8443": "token=***",
        "access_token=a:b:c, retrying": "access_token=***, retrying",
        "api_key=a:b:c; retrying": "api_key=***; retrying",
        "(token=a:b)": "(token=***)",
        "[token=a:b]": "[token=***]",
        "{token=a:b}": "{token=***}",
        "token=a:b&mode=fast": "token=***&mode=fast",
        "token=a:b#fragment": "token=***#fragment",
        "token=": "token=***",
        "(api_key=)": "(api_key=***)",
        "token=A:B key=C:D access_token=E:F": "token=*** key=*** access_token=***",
        "token=A:B, api_key=C:D; key=E:F": "token=***, api_key=***; key=***",
        "TOKEN=A:B Api_Key=C:D Access_Token=E:F KEY=G:H": "TOKEN=*** Api_Key=*** Access_Token=*** KEY=***",
    }
    leaked_values = (
        "abc:def",
        "part1:part2",
        "a:b:c",
        "host:8443",
        "[::1]:8443",
        "A:B",
        "C:D",
        "E:F",
        "G:H",
    )
    for raw, expected in exact_cases.items():
        sanitized = downloader._sanitize_subprocess_output_line(raw)
        _assert(sanitized == expected, f"standalone colon assignment mismatch: {raw!r} -> {sanitized!r}")
        for value in leaked_values:
            _assert(value not in sanitized, f"standalone secret value leaked: {value!r} in {sanitized!r}")

    diagnostic = downloader._sanitize_subprocess_output_line("token=abc:def: request failed")
    _assert(diagnostic.endswith(": request failed"), f"diagnostic suffix was not preserved: {diagnostic!r}")


def _test_url_and_secret_regression() -> None:
    api_key = "AI" "za" + ("X" * 32)
    lines = [
        "https://example.test/path?key=SECRET_VALUE",
        "https://example.test/path?KEY=SECRET_VALUE",
        "https://example.test/path?access_token=TOKEN_VALUE",
        "https://example.test/path?Api_Key=SECRET_VALUE",
        "https://example.test/path?TOKEN=SECRET_VALUE",
        "https://example.test/path?Access_Token=SECRET_VALUE",
        "https://example.test/path?mode=fast&key=SECRET_VALUE",
        api_key,
        "Cookie: SID=secret",
        r"--cookies D:\Secret\cookies.txt",
    ]
    sanitized = "\n".join(downloader._sanitize_subprocess_output_line(line) for line in lines)
    for value in ("SECRET_VALUE", "TOKEN_VALUE", "AI" "za", "SID=secret", r"D:\Secret", "--cookies"):
        _assert(value not in sanitized, f"secret or cookie detail leaked: {value}")
    _assert("https://example.test/path" in sanitized, "URL structure was corrupted")
    _assert("key=***" in sanitized, "key query parameter was not redacted")
    _assert("access_token=***" in sanitized, "access_token query parameter was not redacted")
    _assert("mode=fast" in sanitized, "harmless query parameter was not preserved")
    for placeholder in ("__S9H_PROTECTED_URL_", "<path>"):
        _assert(placeholder not in sanitized, f"URL/path placeholder leaked into URL secret output: {placeholder}")


def _test_exception_fields_safe() -> None:
    stderr = "\n".join(
        [
            r"Error opening output D:\Secret\User\NoExtensionDir: No such file or directory",
            "Could not open C:/Users/Alice/Private Folder/outputdir: Permission denied",
            "Could not open /home/alice/private/outputdir: Permission denied",
            r"Could not open \\server\private-share\secret-folder: Access is denied",
            "https://example.test/path?key=SECRET_VALUE",
            "https://example.test/path?mode=fast&key=SECRET#section,",
            "token=abc:def: request failed",
            "Cookie: SID=secret",
            "Invalid data found when processing input",
        ]
    )
    exc = _capture_ffmpeg_error(_FakeProcess(stderr=stderr, returncode=5))
    stored = "\n".join([*exc.output_lines, exc.combined_output])
    forbidden = (
        r"D:\Secret\User",
        "C:/Users/Alice",
        "/home/alice/private",
        "Alice",
        "alice",
        "server",
        "private-share",
        "abc",
        "def",
        "SECRET",
        "SECRET_VALUE",
        "SECRET#section",
        "SID=secret",
    )
    for value in forbidden:
        _assert(value not in stored, f"exception retained unsafe output: {value}")
    for value in ("No such file or directory", "Permission denied", "Access is denied"):
        _assert(value in stored, f"exception lost useful marker: {value}")
    for value in ("mode=fast", "key=***", "#section", ","):
        _assert(value in stored, f"exception lost URL structure marker: {value}")
    _assert("token=***: request failed" in stored, "exception lost standalone secret diagnostic suffix")
    _assert("__S9H_PROTECTED_URL_" not in stored, "exception retained an internal URL placeholder")
    _assert(len(exc.output_lines) <= downloader.FFMPEG_OUTPUT_LINE_LIMIT, "exception output_lines unbounded")
    _assert(len(exc.combined_output) <= downloader.FFMPEG_COMBINED_OUTPUT_LIMIT, "exception combined_output unbounded")
    _assert(
        downloader.classify_ffmpeg_failure_kind(exc) != downloader.FFmpegFailureKind.UNKNOWN,
        "exception evidence was not classifiable",
    )


def _test_banner_and_progress_filtering() -> None:
    stderr = "\n".join(
        [
            "ffmpeg version 7.0 Copyright (c)",
            "built with gcc 13",
            "configuration: --enable-gpl --enable-libmp3lame",
            "libavutil      59.  8.100 / 59.  8.100",
            "frame=  101 fps=0.0 q=0.0 size=128kB time=00:00:04.00 bitrate=256.0kbits/s speed=8x",
            "Invalid data found when processing input",
        ]
    )
    lines = downloader._ffmpeg_output_lines("", stderr)
    joined = "\n".join(lines)
    _assert("Invalid data found when processing input" in joined, "final failure was not retained")
    _assert("ffmpeg version" not in joined, "version banner was retained")
    _assert("configuration:" not in joined, "build configuration was retained")
    _assert("libavutil" not in joined, "library version row was retained")
    _assert("frame=" not in joined, "progress row was retained")


def _test_cancellation() -> None:
    controller = downloader.DownloadController()
    terminations = []

    def request_cancel(process):
        controller._cancel_requested.set()

    process = _TimeoutProcess(request_cancel)

    def terminate(process_to_terminate):
        terminations.append(process_to_terminate)
        process_to_terminate.returncode = -15

    with _patched_many(
        [
            (downloader.subprocess, "Popen", lambda *args, **kwargs: process),
            (downloader, "_terminate_process_tree", terminate),
        ]
    ):
        try:
            downloader._run_ffmpeg_for_audio(["ffmpeg.exe", "-i", "input.mp4", "output.mp3"], controller)
        except downloader.DownloadCancelled:
            pass
        except downloader.FFmpegExecutionError as exc:
            raise AssertionError(f"cancellation became FFmpegExecutionError: {exc}") from exc
        else:
            raise AssertionError("expected DownloadCancelled")

    _assert(terminations, "process tree was not terminated on cancellation")
    _assert(controller.current_process is None, "current process was not cleared")


def _test_process_not_found() -> None:
    def raise_missing(*args, **kwargs):
        raise FileNotFoundError("missing")

    with _patched(downloader.subprocess, "Popen", raise_missing):
        try:
            downloader._run_ffmpeg_for_audio(["ffmpeg.exe"])
        except downloader.DownloadError as exc:
            _assert(str(exc) == "ffmpeg.exe missing", f"wrong missing-ffmpeg error: {exc}")
        else:
            raise AssertionError("expected DownloadError for missing ffmpeg")


def _test_process_creation_os_error() -> None:
    def raise_permission(*args, **kwargs):
        raise PermissionError(r"Access is denied: C:\Users\Alice\blocked.exe")

    with _patched(downloader.subprocess, "Popen", raise_permission):
        try:
            downloader._run_ffmpeg_for_audio(["ffmpeg.exe"])
        except downloader.FFmpegExecutionError as exc:
            raise AssertionError(f"process creation fabricated FFmpegExecutionError: {exc}") from exc
        except downloader.DownloadError as exc:
            message = str(exc)
            _assert("PermissionError" in message, "exception type was not preserved")
            _assert("extract_mp3" in message, "operation was not preserved")
            _assert("C:\\Users\\Alice" not in message and "Alice" not in message, "path/user leaked")
            _assert(not hasattr(exc, "exit_code"), "process creation failure fabricated an exit code")
        else:
            raise AssertionError("expected DownloadError for process creation OSError")


def _test_outer_loop_friendly_log() -> None:
    logs = []
    updates = []
    progress_events = []
    status_updates = []
    output = downloader._sanitize_subprocess_output_line(
        r"D:\Youtube Downloader Source\data\.s9h-stage\video.mp4: Invalid data found when processing input key=SECRET"
    )
    ffmpeg_error = downloader.FFmpegExecutionError(
        operation="extract_mp3",
        exit_code=7,
        message="ffmpeg extract_mp3 failed: invalid_input",
        output_lines=[output],
        combined_output=output,
    )

    def update_state(channel_id, channel_name, base_folder, video, paths, part, status, download_mode):
        updates.append((part, status))

    patches = [
        (downloader, "validate_download_environment", lambda options: None),
        (downloader, "ensure_output_dirs", lambda base_folder, channel_name, download_mode, channel_id="": None),
        (downloader, "_call_runtime_tool_summary", lambda options, log, cancel_controller: None),
        (downloader, "get_video_entry", lambda channel_id, video_id: object()),
        (downloader, "is_mode_complete", lambda entry, mode: False),
        (downloader, "missing_parts_for_mode", lambda entry, mode: (PART_AUDIO,)),
        (downloader, "_missing_parts_for_current_paths", lambda options, video, paths, mode_parts: (PART_AUDIO,)),
        (downloader, "_premiere_safe_mp4_ready_for_download", lambda path, cancel_controller=None: True),
        (downloader, "_extract_mp3_from_video", lambda *args, **kwargs: (_ for _ in ()).throw(ffmpeg_error)),
        (downloader, "update_video_part_state", update_state),
        (
            downloader,
            "reconcile_downloaded_item_state",
            lambda *args, **kwargs: ("partial", STATUS_ERROR),
        ),
        (downloader, "get_effective_status", lambda entry, mode: STATUS_ERROR),
    ]

    with TemporaryDirectory() as temp_dir:
        video = _video()
        options = downloader.DownloadOptions(
            temp_dir,
            "channel",
            "Channel",
            download_mode=MODE_VIDEO_AUDIO_THUMB,
            file_start_number=1,
        )
        with _patched_many(patches):
            downloader.download_items(
                [video],
                options,
                logs.append,
                status_updates.append,
                progress_callback=progress_events.append,
            )

    expected_title = friendly_ffmpeg_failure_kind_error("invalid_input").title
    log_text = "\n".join(logs)
    _assert((PART_AUDIO, STATUS_ERROR) in updates, "audio part was not marked error")
    _assert((PART_VIDEO, STATUS_ERROR) not in updates, "video part was incorrectly marked error")
    _assert(expected_title in log_text, "friendly FFmpeg title missing from log")
    _assert("ffmpeg exit code 7" in log_text, "exit code missing from log")
    _assert("operation: extract_mp3" in log_text, "operation missing from log")
    _assert("Invalid data found when processing input" in log_text, "sanitized technical line missing")
    for forbidden in (r"D:\Youtube Downloader Source", "SECRET", "audio extraction failed"):
        _assert(forbidden not in log_text, f"forbidden log detail leaked: {forbidden}")
    _assert(any(getattr(event, "kind", "") == "error" for event in progress_events), "error progress missing")


def _test_zero_byte_output_after_exit_zero() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "source.mp4"
        source.write_bytes(b"mp4")
        final = root / "final.mp3"
        staging = root / "stage"
        staging.mkdir()

        with _patched_many(
            [
                (downloader, "_validate_premiere_safe_mp4_for_download", lambda *args, **kwargs: None),
                (downloader, "_run_ffmpeg_for_audio", lambda *args, **kwargs: ""),
            ]
        ):
            try:
                downloader._extract_mp3_from_video(source, staging, final)
            except downloader.FFmpegExecutionError as exc:
                raise AssertionError(f"zero-byte validation fabricated FFmpegExecutionError: {exc}") from exc
            except downloader.DownloadError as exc:
                _assert(str(exc) == "audio extraction failed", f"wrong validation error: {exc}")
            else:
                raise AssertionError("expected zero-byte/missing staged MP3 validation failure")

        existing = root / "existing.mp3"
        existing.write_bytes(b"existing mp3")

        def fail_if_called(*args, **kwargs):
            raise AssertionError("ffmpeg should not run when final MP3 already exists")

        with _patched_many(
            [
                (downloader, "_validate_premiere_safe_mp4_for_download", lambda *args, **kwargs: None),
                (downloader, "_run_ffmpeg_for_audio", fail_if_called),
            ]
        ):
            downloader._extract_mp3_from_video(source, staging, existing)
        _assert(existing.read_bytes() == b"existing mp3", "existing final MP3 was changed")


def _test_success_promotes_mp3() -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "source.mp4"
        source.write_bytes(b"mp4")
        staging = root / "stage"
        staging.mkdir()
        final = root / "final.mp3"

        def fake_ffmpeg(command, cancel_controller=None):
            Path(command[-1]).write_bytes(b"mp3")
            return ""

        with _patched_many(
            [
                (downloader, "_validate_premiere_safe_mp4_for_download", lambda *args, **kwargs: None),
                (downloader, "_run_ffmpeg_for_audio", fake_ffmpeg),
            ]
        ):
            downloader._extract_mp3_from_video(source, staging, final)

        _assert(final.exists() and final.read_bytes() == b"mp3", "final MP3 was not promoted")


def _ffmpeg_exc(output: str) -> downloader.FFmpegExecutionError:
    lines = [
        downloader._sanitize_subprocess_output_line(line)
        for line in str(output or "").splitlines()
        if downloader._sanitize_subprocess_output_line(line)
    ]
    combined = "\n".join(lines)
    return downloader.FFmpegExecutionError(
        operation="extract_mp3",
        exit_code=1,
        message="ffmpeg extract_mp3 failed",
        output_lines=lines,
        combined_output=combined,
    )


def _video():
    return SimpleNamespace(
        video_id="ffmpeg-video",
        title="FFmpeg Video",
        sanitized_filename_base="ffmpeg-video",
        display_order=1,
        thumbnail_url="",
        status=STATUS_DOWNLOADED,
    )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
