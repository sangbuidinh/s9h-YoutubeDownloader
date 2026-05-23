import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.app_settings import app_settings_file
from core.runtime_paths import app_root, data_dir, db_file, is_frozen, runtime_file


RUNTIME_FILES = (
    ("yt-dlp", "yt-dlp.exe"),
    ("ffmpeg", "ffmpeg.exe"),
    ("deno", "deno.exe"),
    ("api_key", "api key.txt"),
)


def main() -> int:
    _configure_stdio()
    mode = "frozen" if is_frozen() else "source"
    print("Runtime paths")
    print(f"mode: {mode}")
    print(f"is_frozen: {is_frozen()}")
    print(f"app_root: {app_root()}")
    print(f"data_dir: {data_dir()}")
    print(f"db_path: {db_file()}")
    print(f"settings_path: {app_settings_file()}")
    print("external_runtime_files:")
    for label, filename in RUNTIME_FILES:
        path = runtime_file(filename)
        print(f"  {label}: {path} exists={path.exists()}")
    print("sqlite_sidecars:")
    print(f"  wal: {Path(str(db_file()) + '-wal')}")
    print(f"  shm: {Path(str(db_file()) + '-shm')}")
    return 0


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
