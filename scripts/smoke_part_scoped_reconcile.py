import inspect
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, downloader, state_store
from core.download_modes import DOWNLOAD_MODES, PART_AUDIO, PART_THUMB, PART_VIDEO, required_parts


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"
SAVE_BASE_FOLDER = "D:/A"
KNOWN_PARTS = (PART_VIDEO, PART_THUMB, PART_AUDIO)


def main() -> int:
    _configure_stdio()
    real_runtime_before = _snapshot_real_runtime_files()
    _assert_contracts()

    scenarios = _generated_scenarios()
    sqlite_runs = 0
    json_runs = 0
    for scenario in scenarios:
        _run_sqlite_scenario(scenario)
        sqlite_runs += 1
        _run_json_scenario(scenario)
        json_runs += 1

    _assert(
        real_runtime_before == _snapshot_real_runtime_files(),
        "real runtime state files were mutated by temp-file smoke tests",
    )
    mode_part_count = sum(len(required_parts(mode)) for mode in DOWNLOAD_MODES)
    print(
        "part-scoped reconcile smoke tests passed: "
        f"mode_parts={mode_part_count} scenarios={len(scenarios)} sqlite={sqlite_runs} json={json_runs}"
    )
    return 0


def _generated_scenarios() -> list[dict]:
    scenarios = []
    for download_mode in DOWNLOAD_MODES:
        required = tuple(required_parts(download_mode))
        for run_part in required:
            scenarios.append(
                {
                    "name": "retry one missing part while unowned paths are stale",
                    "download_mode": download_mode,
                    "required": required,
                    "run_parts": (run_part,),
                    "existing_current_run_files": (run_part,),
                    "initial_downloaded_parts": tuple(part for part in required if part != run_part),
                    "initial_failed_or_missing_parts": (run_part,),
                    "expect_complete": True,
                }
            )
            scenarios.append(
                {
                    "name": "failed retry does not downgrade unowned downloaded parts",
                    "download_mode": download_mode,
                    "required": required,
                    "run_parts": (run_part,),
                    "existing_current_run_files": (),
                    "initial_downloaded_parts": tuple(part for part in required if part != run_part),
                    "initial_failed_or_missing_parts": (run_part,),
                    "expect_complete": False,
                }
            )
    return scenarios


def _run_sqlite_scenario(scenario: dict) -> None:
    with _temp_runtime() as paths:
        video = _video(_scenario_video_id("sqlite", scenario))
        initial_parts = _initial_parts(scenario)
        _seed_sqlite_state(paths["db_path"], video, initial_parts, scenario["download_mode"])
        run_paths = _run_paths(paths["data_dir"], scenario["existing_current_run_files"])

        with _patched_sqlite_db(paths["db_path"]):
            before = db_store.get_video_entry(CHANNEL_ID, video.video_id)
            state_store._sqlite_reconcile_downloaded_item_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                SAVE_BASE_FOLDER,
                video,
                run_paths,
                scenario["download_mode"],
                run_parts=scenario["run_parts"],
            )
            after = db_store.get_video_entry(CHANNEL_ID, video.video_id)

        _assert_scenario_result("sqlite", scenario, before, after)


def _run_json_scenario(scenario: dict) -> None:
    with _temp_runtime() as paths:
        video = _video(_scenario_video_id("json", scenario))
        _write_json_state(paths["json_path"], video, _initial_parts(scenario), scenario["download_mode"])
        run_paths = _run_paths(paths["data_dir"], scenario["existing_current_run_files"])

        with _patched_json_paths(paths):
            before = state_store._json_get_video_entry(CHANNEL_ID, video.video_id)
            state_store._json_reconcile_downloaded_item_state(
                CHANNEL_ID,
                CHANNEL_NAME,
                SAVE_BASE_FOLDER,
                video,
                run_paths,
                scenario["download_mode"],
                run_parts=scenario["run_parts"],
            )
            after = state_store._json_get_video_entry(CHANNEL_ID, video.video_id)

        _assert_scenario_result("json", scenario, before, after)


def _initial_parts(scenario: dict) -> dict[str, tuple[str, str, str]]:
    parts = {}
    for part in scenario["initial_downloaded_parts"]:
        parts[part] = (state_store.STATUS_DOWNLOADED, _old_filename(part), _old_path(part))
    for part in scenario["initial_failed_or_missing_parts"]:
        parts[part] = (state_store.STATUS_ERROR, _old_filename(part), _old_path(part))
    return parts


def _seed_sqlite_state(db_path: Path, video, parts: dict[str, tuple[str, str, str]], download_mode: str) -> None:
    for part, (status, filename, file_path) in parts.items():
        db_store.update_video_part_state(
            CHANNEL_ID,
            video.video_id,
            part,
            filename=filename,
            file_path=file_path,
            status=status,
            path=db_path,
            save_base_folder=SAVE_BASE_FOLDER,
            download_mode=download_mode,
            channel_name=CHANNEL_NAME,
            original_title=video.title,
            sanitized_filename_base=video.sanitized_filename_base,
            display_order_at_download=video.display_order,
        )


def _write_json_state(
    path: Path,
    video,
    parts: dict[str, tuple[str, str, str]],
    download_mode: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "channel_id": CHANNEL_ID,
        "channel_name": CHANNEL_NAME,
        "save_base_folder": SAVE_BASE_FOLDER,
        "video_id": video.video_id,
        "original_title": video.title,
        "sanitized_filename_base": video.sanitized_filename_base,
        "display_order_at_download": video.display_order,
    }
    for part, (status, filename, file_path) in parts.items():
        entry[f"{part}_status"] = status
        entry[f"{part}_filename"] = filename
        entry[f"{part}_path"] = file_path
    entry["status"] = state_store.get_effective_status(entry, download_mode)
    state = {
        "version": 1,
        "channels": {
            CHANNEL_ID: {
                "channel_id": CHANNEL_ID,
                "channel_name": CHANNEL_NAME,
                "save_base_folder": SAVE_BASE_FOLDER,
                "videos": {video.video_id: entry},
            }
        },
    }
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_paths(data_dir: Path, existing_current_run_files: tuple[str, ...]):
    paths = SimpleNamespace(
        video_path=data_dir / "current" / "video" / "current_video.mp4",
        thumb_path=data_dir / "current" / "thumb" / "current_thumb.jpg",
        audio_path=data_dir / "current" / "audio" / "current_audio.mp3",
    )
    for part in existing_current_run_files:
        path = _path_for_part(paths, part)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return paths


def _assert_scenario_result(backend: str, scenario: dict, before: dict | None, after: dict | None) -> None:
    prefix = f"{backend} {scenario['download_mode']} {scenario['run_parts']} {scenario['name']}"
    _assert(isinstance(after, dict), f"{prefix}: missing final entry")

    run_parts = set(scenario["run_parts"])
    existing_current_run_files = set(scenario["existing_current_run_files"])
    expected_downloaded = set(scenario["initial_downloaded_parts"])
    expected_downloaded.update(part for part in run_parts if part in existing_current_run_files)

    for part in scenario["required"]:
        before_status = state_store.part_status_from_entry(before, part)
        after_status = state_store.part_status_from_entry(after, part)
        if part not in run_parts and before_status == state_store.STATUS_DOWNLOADED:
            _assert(after_status == state_store.STATUS_DOWNLOADED, f"{prefix}: unowned {part} was downgraded")
            _assert(after.get(f"{part}_filename") == before.get(f"{part}_filename"), f"{prefix}: unowned {part} filename changed")
            _assert(after.get(f"{part}_path") == before.get(f"{part}_path"), f"{prefix}: unowned {part} path changed")
        if part in run_parts and part in existing_current_run_files:
            _assert(after_status == state_store.STATUS_DOWNLOADED, f"{prefix}: run-owned {part} was not promoted")
        if part in run_parts and part not in existing_current_run_files:
            _assert(after_status == before_status, f"{prefix}: failed run-owned {part} changed without output")

    for part in expected_downloaded:
        _assert(
            state_store.part_status_from_entry(after, part) == state_store.STATUS_DOWNLOADED,
            f"{prefix}: expected downloaded {part} is not downloaded",
        )

    missing_after = set(state_store.missing_parts_for_mode(after, scenario["download_mode"]))
    unowned_missing = missing_after - run_parts
    _assert(not unowned_missing, f"{prefix}: aggregate reports missing unowned parts {sorted(unowned_missing)}")

    final_status = state_store.get_effective_status(after, scenario["download_mode"])
    _assert(after.get("status") == final_status, f"{prefix}: persisted aggregate status is stale")
    if scenario["expect_complete"]:
        _assert(final_status == state_store.STATUS_DOWNLOADED, f"{prefix}: aggregate did not become downloaded")
    else:
        _assert(final_status != state_store.STATUS_MISSING_VIDEO or PART_VIDEO in run_parts, f"{prefix}: unrelated video miss")


def _assert_contracts() -> None:
    _assert(
        "run_parts" in inspect.signature(downloader._reconcile_current_item).parameters,
        "downloader._reconcile_current_item does not accept run_parts",
    )
    _assert(
        "run_parts" in inspect.signature(state_store.reconcile_downloaded_item_state).parameters,
        "state_store.reconcile_downloaded_item_state does not accept run_parts",
    )
    common_update_keys = set(state_store._sqlite_common_video_updates(CHANNEL_NAME, _video("contract"), None))
    forbidden = {
        "video_filename",
        "thumb_filename",
        "audio_filename",
        "video_path",
        "thumb_path",
        "audio_path",
        "video_status",
        "thumb_status",
        "audio_status",
    }
    _assert(not common_update_keys & forbidden, f"common updates emit part fields: {sorted(common_update_keys & forbidden)}")


def _scenario_video_id(backend: str, scenario: dict) -> str:
    part = scenario["run_parts"][0]
    suffix = "success" if scenario["expect_complete"] else "failed"
    safe_mode = "".join(ch if ch.isalnum() else "-" for ch in scenario["download_mode"]).strip("-").lower()
    return f"{backend}-{safe_mode}-{part}-{suffix}"


def _video(video_id: str):
    return SimpleNamespace(
        video_id=video_id,
        title=f"Video {video_id}",
        sanitized_filename_base=video_id,
        display_order=1,
    )


def _old_filename(part: str) -> str:
    if part == PART_VIDEO:
        return "old_video.mp4"
    if part == PART_THUMB:
        return "old_thumb.jpg"
    if part == PART_AUDIO:
        return "old_audio.mp3"
    return f"old_{part}.bin"


def _old_path(part: str) -> str:
    folder = {
        PART_VIDEO: "video",
        PART_THUMB: "thumb",
        PART_AUDIO: "audio",
    }.get(part, part)
    return f"D:/A/Channel/{folder}/{_old_filename(part)}"


def _path_for_part(paths, part: str) -> Path:
    if part == PART_VIDEO:
        return paths.video_path
    if part == PART_THUMB:
        return paths.thumb_path
    if part == PART_AUDIO:
        return paths.audio_path
    raise ValueError(f"Unsupported test part: {part}")


@contextmanager
def _temp_runtime():
    with TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir) / "data"
        yield {
            "data_dir": data_dir,
            "json_path": data_dir / "download_state.json",
            "db_path": data_dir / "download_state.sqlite3",
        }


@contextmanager
def _patched_sqlite_db(db_path: Path):
    old_db_file = db_store.db_file
    try:
        db_store.db_file = lambda: db_path
        yield
    finally:
        db_store.db_file = old_db_file


@contextmanager
def _patched_json_paths(paths: dict):
    old_state_file = state_store.state_file
    old_data_dir = state_store.data_dir
    try:
        state_store.state_file = lambda: paths["json_path"]
        state_store.data_dir = lambda: paths["data_dir"]
        yield
    finally:
        state_store.state_file = old_state_file
        state_store.data_dir = old_data_dir


def _snapshot_real_runtime_files() -> dict[str, tuple[bool, int | None, int | None]]:
    paths = {
        "json": state_store.state_file(),
        "sqlite": db_store.db_file(),
        "wal": Path(f"{db_store.db_file()}-wal"),
        "shm": Path(f"{db_store.db_file()}-shm"),
    }
    snapshot = {}
    for label, path in paths.items():
        try:
            stat = path.stat()
        except FileNotFoundError:
            snapshot[label] = (False, None, None)
        else:
            snapshot[label] = (True, stat.st_size, stat.st_mtime_ns)
    return snapshot


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
