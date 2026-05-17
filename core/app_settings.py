import json
import tempfile
from pathlib import Path

from core.runtime_paths import data_dir


def app_settings_file() -> Path:
    return data_dir() / "app_settings.json"


def load_app_settings() -> dict:
    settings_path = app_settings_file()
    if not settings_path.exists():
        return {}
    try:
        with settings_path.open("r", encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except (OSError, json.JSONDecodeError):
        return {}
    return settings if isinstance(settings, dict) else {}


def load_last_api_key() -> str:
    last_api_key = load_app_settings().get("last_api_key", "")
    return last_api_key.strip() if isinstance(last_api_key, str) else ""


def save_last_api_key(api_key: str) -> bool:
    key = (api_key or "").strip()
    if not key:
        return False

    settings = load_app_settings()
    settings["last_api_key"] = key
    return _save_app_settings(settings)


def _save_app_settings(settings: dict) -> bool:
    settings_data_dir = data_dir()
    try:
        settings_data_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=".app_settings_",
            suffix=".tmp",
            dir=str(settings_data_dir),
            text=True,
        )
        temp_path = Path(temp_name)
        try:
            with open(fd, "w", encoding="utf-8") as temp_file:
                json.dump(settings, temp_file, ensure_ascii=False, indent=2)
                temp_file.write("\n")
            temp_path.replace(app_settings_file())
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False
