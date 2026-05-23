import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_EXE = REPO_ROOT / "dist" / "Youtube Downloaderbs.exe"


PREFLIGHT_COMMANDS = (
    (
        sys.executable,
        "-m",
        "py_compile",
        "app.py",
        "core\\app_settings.py",
        "core\\downloader.py",
        "core\\error_messages.py",
        "core\\file_status.py",
        "core\\filename_utils.py",
        "core\\runtime_paths.py",
        "core\\state_store.py",
        "core\\db_store.py",
        "core\\state_migration.py",
        "core\\youtube_api.py",
        "ui\\dialogs.py",
        "ui\\main_window.py",
        "scripts\\migrate_download_state_to_sqlite.py",
    ),
)


def main() -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Run preflight checks and build the Windows executable.")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run packaging preflight checks without invoking PyInstaller.",
    )
    args = parser.parse_args()

    print("Packaging preflight")
    print(f"repo_root: {REPO_ROOT}")
    print("pyinstaller_mode: onefile windowed app.py")
    for command in PREFLIGHT_COMMANDS:
        _run(command)

    if args.preflight_only:
        print("preflight_only: build skipped")
        return 0

    pyinstaller = _pyinstaller_command()
    _run(
        (
            *pyinstaller,
            "--noconfirm",
            "--clean",
            "--onefile",
            "--windowed",
            "--name",
            "Youtube Downloaderbs",
            "app.py",
        )
    )

    print("Build completed")
    print(f"expected_exe: {DIST_EXE}")
    print("Post-build runtime files must remain external and be placed next to the exe:")
    print("  data/download_state.sqlite3 or data/download_state.json")
    print("  data/app_settings.json")
    print("  data/bin/yt-dlp.exe")
    print("  data/bin/ffmpeg.exe")
    print("  data/bin/deno.exe if needed")
    print("  data/api key.txt if used")
    print("  user-selected cookies files if used")
    return 0


def _run(command: tuple[str, ...]) -> None:
    print(f"> {' '.join(command)}")
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def _pyinstaller_command() -> tuple[str, ...]:
    executable = shutil.which("pyinstaller")
    if executable:
        return (executable,)
    return (sys.executable, "-m", "PyInstaller")


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
