import sys
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader


VIDEO_FORMAT_ID = "137"
AUDIO_FORMAT_ID = "140"
MEDIA_DOWNLOADER_OPTIONS = {
    "--downloader",
    "--external-downloader",
    "--downloader-args",
    "--external-downloader-args",
}


def main() -> int:
    _test_fast_saved_metadata_media_commands_use_native_ytdlp()
    _test_combined_saved_metadata_command_uses_native_ytdlp()
    _test_watchdog_contract_is_absent_from_production()
    print("fast native video smoke passed")
    return 0


def _test_fast_saved_metadata_media_commands_use_native_ytdlp() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        info_json_path = root / "snapshot.info.json"
        base_command = [
            "yt-dlp.exe",
            "-N",
            "16",
            "--downloader",
            "aria2c.exe",
            "--downloader-args",
            "aria2c:-x 16 -s 16 -j 16 -k 1M",
            "--external-downloader",
            "aria2c.exe",
            "--external-downloader-args",
            "aria2c:-x 16 -s 16 -j 16 -k 1M",
            "--limit-rate",
            "2M",
            "-f",
            "bestvideo+bestaudio/best",
            "-o",
            str(root / "source.%(ext)s"),
            "https://www.youtube.com/watch?v=video-id",
        ]
        selection = downloader._HybridFormatSelection(
            kind="split",
            video_format_id=VIDEO_FORMAT_ID,
            audio_format_id=AUDIO_FORMAT_ID,
            video_bytes=100,
            audio_bytes=10,
            duration_seconds=60.0,
        )

        video_command, audio_command = downloader._build_fast_hybrid_media_commands(
            base_command,
            info_json_path,
            selection,
            root,
        )

    _assert(audio_command is not None, "Split selection lost companion audio")
    _assert_native_media_command(video_command, "video")
    _assert_native_media_command(audio_command, "companion audio")
    _assert(_option_value(video_command, "-N") == "1", "Fast video did not use native -N 1")
    _assert(_option_value(audio_command, "-N") == "1", "Companion audio did not use native -N 1")
    _assert(_option_value(video_command, "-f") == VIDEO_FORMAT_ID, "Fast video format ID changed")
    _assert(_option_value(audio_command, "-f") == AUDIO_FORMAT_ID, "Companion audio format ID changed")
    _assert(_option_value(video_command, "--limit-rate") == "2M", "Fast video speed limit was lost")
    _assert(_option_value(audio_command, "--limit-rate") == "2M", "Companion audio speed limit was lost")
    _assert(
        _option_value(video_command, "--load-info-json")
        == _option_value(audio_command, "--load-info-json")
        == str(info_json_path),
        "Fast media legs did not reuse one saved metadata snapshot",
    )
    _assert(
        not any(value.startswith("https://") for value in video_command + audio_command),
        "Saved-metadata media command retained an extractor URL",
    )


def _test_combined_saved_metadata_command_uses_native_ytdlp() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        info_json_path = root / "combined.info.json"
        selection = downloader._HybridFormatSelection(
            kind="combined",
            video_format_id="combined-format-id",
            audio_format_id=None,
            video_bytes=100,
            audio_bytes=None,
            duration_seconds=60.0,
        )
        media_command, audio_command = downloader._build_fast_hybrid_media_commands(
            [
                "yt-dlp.exe",
                "-N",
                "16",
                "--downloader",
                "aria2c.exe",
                "--downloader-args",
                "aria2c:-x 16 -s 16 -j 16 -k 1M",
                "-f",
                "bestvideo+bestaudio/best",
                "-o",
                str(root / "source.%(ext)s"),
                "https://www.youtube.com/watch?v=video-id",
            ],
            info_json_path,
            selection,
            root,
        )

    _assert(audio_command is None, "Combined selection invented companion audio")
    _assert_native_media_command(media_command, "combined video")
    _assert(_option_value(media_command, "-N") == "1", "Combined video did not use native -N 1")
    _assert(
        _option_value(media_command, "-f") == selection.video_format_id,
        "Combined exact format ID changed",
    )
    _assert(
        _option_value(media_command, "--load-info-json") == str(info_json_path),
        "Combined video did not reuse the saved metadata snapshot",
    )


def _test_watchdog_contract_is_absent_from_production() -> None:
    source = (REPO_ROOT / "core" / "downloader.py").read_text(encoding="utf-8")
    for forbidden in (
        "35_780_971",
        "FAST_VIDEO_SLOW_",
        "_FastVideoPerformance",
        "_FAST_VIDEO_PERFORMANCE_WATCHDOG_CONTEXT",
        "performance_fallback",
        "aria2_before_fallback_seconds",
        "Fast transfer is slow; switching transport...",
    ):
        _assert(forbidden not in source, f"Production retained watchdog contract: {forbidden}")


def _assert_native_media_command(command: list[str], label: str) -> None:
    present = sorted(option for option in MEDIA_DOWNLOADER_OPTIONS if option in command)
    _assert(not present, f"{label} retained external downloader options: {present}")


def _option_value(command: list[str], option: str) -> str:
    try:
        return command[command.index(option) + 1]
    except (ValueError, IndexError) as exc:
        raise AssertionError(f"Missing {option} in command: {command}") from exc


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
