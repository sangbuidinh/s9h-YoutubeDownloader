import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
import tempfile
from pathlib import Path

from core.runtime_paths import data_dir


COOKIE_SOURCE_FILE = "file"
COOKIE_SOURCE_BRIDGE = "bridge"
COOKIE_SOURCE_VALUES = {COOKIE_SOURCE_FILE, COOKIE_SOURCE_BRIDGE}
DEFAULT_BRIDGE_COOKIE_PATH = ""
LEGACY_BRIDGE_COOKIE_PATH = r"D:\s9h-youtube-cookie-bridge\data\runtime\youtube_cookies.txt"
COOKIE_PATH_MAX_CHARS = 32767
BRIDGE_COOKIE_PATH_FIELD = "bridge_cookie_path"

API_KEY_PROTECTED_FIELD = "last_api_key_protected"
API_KEY_PLAINTEXT_FIELD = "last_api_key"
API_KEY_REMEMBER_FIELD = "remember_api_key"  # Obsolete; removed on the next successful settings write.
API_KEY_PROTECTION_PROVIDER = "windows_dpapi_current_user"
API_KEY_PROTECTION_VERSION = 1
API_KEY_OPTIONAL_ENTROPY = b"s9h-YoutubeDownloader/api-key/v1"
API_KEY_MAX_BYTES = 4096
API_KEY_MAX_CIPHERTEXT_BYTES = 16 * 1024
CRYPTPROTECT_UI_FORBIDDEN = 0x1


@dataclass(frozen=True)
class ApiKeyPersistenceState:
    api_key: str
    storage_available: bool
    status: str = ""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


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


def secure_api_key_storage_available() -> bool:
    try:
        return _secure_api_key_storage_available()
    except Exception:
        return False


def load_api_key_persistence_state() -> ApiKeyPersistenceState:
    settings = load_app_settings()
    storage_available = secure_api_key_storage_available()
    has_legacy_key = API_KEY_PLAINTEXT_FIELD in settings
    legacy_key = _validated_api_key(settings.get(API_KEY_PLAINTEXT_FIELD))
    has_protected_key = API_KEY_PROTECTED_FIELD in settings

    if has_legacy_key and legacy_key is None:
        cleaned_settings = dict(settings)
        cleaned_settings.pop(API_KEY_PLAINTEXT_FIELD, None)
        cleaned_settings.pop(API_KEY_REMEMBER_FIELD, None)
        if not _save_app_settings(cleaned_settings):
            return ApiKeyPersistenceState("", storage_available, "settings_write_failed")
        if has_protected_key:
            return _load_protected_api_key(
                cleaned_settings,
                storage_available,
                "invalid_legacy_plaintext_removed",
            )
        return ApiKeyPersistenceState("", storage_available, "invalid_legacy_plaintext_removed")

    if legacy_key is not None:
        if has_protected_key:
            cleaned_settings = dict(settings)
            cleaned_settings.pop(API_KEY_PLAINTEXT_FIELD, None)
            cleaned_settings.pop(API_KEY_REMEMBER_FIELD, None)
            if not _save_app_settings(cleaned_settings):
                return ApiKeyPersistenceState("", storage_available, "settings_write_failed")
            return _load_protected_api_key(
                cleaned_settings,
                storage_available,
                "legacy_plaintext_removed",
            )
        return _migrate_legacy_api_key(settings, legacy_key, storage_available)

    cleanup_failed = False
    if API_KEY_REMEMBER_FIELD in settings:
        cleaned_settings = dict(settings)
        cleaned_settings.pop(API_KEY_REMEMBER_FIELD, None)
        if _save_app_settings(cleaned_settings):
            settings = cleaned_settings
        else:
            cleanup_failed = True

    state = _load_protected_api_key(settings, storage_available, "")
    if cleanup_failed and state.status in {"ok", "not_remembered"}:
        return ApiKeyPersistenceState(state.api_key, storage_available, "settings_write_failed")
    return state


def save_last_api_key(api_key: str) -> bool:
    key = _validated_api_key(api_key)
    if key is None or not secure_api_key_storage_available():
        return False

    try:
        protected_payload = _protected_payload_from_bytes(_protect_api_key_bytes(key.encode("utf-8")))
    except Exception:
        return False

    settings = load_app_settings()
    settings[API_KEY_PROTECTED_FIELD] = protected_payload
    settings.pop(API_KEY_PLAINTEXT_FIELD, None)
    settings.pop(API_KEY_REMEMBER_FIELD, None)
    return _save_app_settings(settings)


def load_cookie_source() -> str:
    source = load_app_settings().get("cookie_source", COOKIE_SOURCE_FILE)
    return source if isinstance(source, str) and source in COOKIE_SOURCE_VALUES else COOKIE_SOURCE_FILE


def save_cookie_source(source: str) -> bool:
    settings = load_app_settings()
    settings["cookie_source"] = source if isinstance(source, str) and source in COOKIE_SOURCE_VALUES else COOKIE_SOURCE_FILE
    return _save_app_settings(settings)


def load_cookies_path() -> str:
    return _normalized_cookie_path(load_app_settings().get("cookies_path"))


def save_cookies_path(path: str) -> bool:
    settings = load_app_settings()
    normalized_path = _normalized_cookie_path(path)
    if normalized_path:
        settings["cookies_path"] = normalized_path
    else:
        settings.pop("cookies_path", None)
    return _save_app_settings(settings)


def load_bridge_cookie_path() -> str:
    settings = load_app_settings()
    if BRIDGE_COOKIE_PATH_FIELD in settings:
        return _normalized_cookie_path(settings[BRIDGE_COOKIE_PATH_FIELD])

    legacy_path = _normalized_cookie_path(LEGACY_BRIDGE_COOKIE_PATH)
    if legacy_path and _is_existing_regular_file(legacy_path):
        return legacy_path
    return DEFAULT_BRIDGE_COOKIE_PATH


def save_bridge_cookie_path(path: str) -> bool:
    settings = load_app_settings()
    normalized_path = _normalized_cookie_path(path)
    settings[BRIDGE_COOKIE_PATH_FIELD] = normalized_path
    return _save_app_settings(settings)


def save_cookie_preferences(source: str, cookies_path: str, bridge_cookie_path: str) -> bool:
    settings = load_app_settings()
    settings["cookie_source"] = source if isinstance(source, str) and source in COOKIE_SOURCE_VALUES else COOKIE_SOURCE_FILE

    normalized_cookies_path = _normalized_cookie_path(cookies_path)
    if normalized_cookies_path:
        settings["cookies_path"] = normalized_cookies_path
    else:
        settings.pop("cookies_path", None)

    normalized_bridge_path = _normalized_cookie_path(bridge_cookie_path)
    settings[BRIDGE_COOKIE_PATH_FIELD] = normalized_bridge_path

    return _save_app_settings(settings)


def _migrate_legacy_api_key(
    settings: dict,
    legacy_key: str,
    storage_available: bool,
) -> ApiKeyPersistenceState:
    if not storage_available:
        cleanup_status = (
            "secure_storage_unavailable" if _remove_legacy_plaintext(settings) else "settings_write_failed"
        )
        return ApiKeyPersistenceState("", storage_available, cleanup_status)

    try:
        protected_payload = _protected_payload_from_bytes(_protect_api_key_bytes(legacy_key.encode("utf-8")))
    except Exception:
        cleanup_status = (
            "secure_storage_unavailable" if _remove_legacy_plaintext(settings) else "settings_write_failed"
        )
        return ApiKeyPersistenceState("", storage_available, cleanup_status)

    migrated = dict(settings)
    migrated[API_KEY_PROTECTED_FIELD] = protected_payload
    migrated.pop(API_KEY_PLAINTEXT_FIELD, None)
    migrated.pop(API_KEY_REMEMBER_FIELD, None)
    if _save_app_settings(migrated):
        return ApiKeyPersistenceState(legacy_key, storage_available, "legacy_migrated")

    _remove_legacy_plaintext(settings)
    return ApiKeyPersistenceState("", storage_available, "settings_write_failed")


def _load_protected_api_key(
    settings: dict,
    storage_available: bool,
    fallback_status: str,
) -> ApiKeyPersistenceState:
    if API_KEY_PROTECTED_FIELD not in settings:
        return ApiKeyPersistenceState("", storage_available, fallback_status or "not_remembered")

    payload = settings[API_KEY_PROTECTED_FIELD]
    protected_bytes = _protected_bytes_from_payload(payload)
    if protected_bytes is None:
        return ApiKeyPersistenceState("", storage_available, "unsupported_payload")
    if not storage_available:
        return ApiKeyPersistenceState("", storage_available, "secure_storage_unavailable")
    try:
        decrypted = _unprotect_api_key_bytes(protected_bytes)
    except Exception:
        return ApiKeyPersistenceState("", storage_available, "decrypt_failed")

    try:
        api_key = decrypted.decode("utf-8").strip()
    except UnicodeDecodeError:
        return ApiKeyPersistenceState("", storage_available, "unsupported_payload")
    if not api_key or len(api_key.encode("utf-8")) > API_KEY_MAX_BYTES:
        return ApiKeyPersistenceState("", storage_available, "unsupported_payload")
    return ApiKeyPersistenceState(api_key, storage_available, fallback_status or "ok")


def _remove_legacy_plaintext(settings: dict) -> bool:
    cleaned = dict(settings)
    cleaned.pop(API_KEY_PLAINTEXT_FIELD, None)
    cleaned.pop(API_KEY_REMEMBER_FIELD, None)
    return _save_app_settings(cleaned)


def _protected_payload_from_bytes(protected_bytes: bytes) -> dict:
    protected_bytes = _validate_protected_bytes(protected_bytes)
    if protected_bytes is None:
        raise ValueError("invalid protected API key payload")
    ciphertext = base64.b64encode(protected_bytes).decode("ascii")
    if not ciphertext:
        raise ValueError("empty protected API key ciphertext")
    return {
        "provider": API_KEY_PROTECTION_PROVIDER,
        "version": API_KEY_PROTECTION_VERSION,
        "ciphertext": ciphertext,
    }


def _protected_bytes_from_payload(payload: object) -> bytes | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("provider") != API_KEY_PROTECTION_PROVIDER:
        return None
    version = payload.get("version")
    if type(version) is not int or version != API_KEY_PROTECTION_VERSION:
        return None
    ciphertext = payload.get("ciphertext")
    if not isinstance(ciphertext, str) or not ciphertext.strip():
        return None
    try:
        protected_bytes = base64.b64decode(ciphertext.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        return None
    if not protected_bytes or len(protected_bytes) > API_KEY_MAX_CIPHERTEXT_BYTES:
        return None
    return protected_bytes


def _normalized_api_key(api_key: object) -> str:
    return api_key.strip() if isinstance(api_key, str) else ""


def _validated_api_key(api_key: object) -> str | None:
    if not isinstance(api_key, str):
        return None
    key = api_key.strip()
    if not key:
        return None
    try:
        key_bytes = key.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(key_bytes) > API_KEY_MAX_BYTES:
        return None
    return key


def _validate_protected_bytes(protected_bytes: object) -> bytes | None:
    if type(protected_bytes) is not bytes:
        return None
    if not protected_bytes or len(protected_bytes) > API_KEY_MAX_CIPHERTEXT_BYTES:
        return None
    return protected_bytes


def _normalized_cookie_path(value: object) -> str:
    if not isinstance(value, str):
        return ""
    path = value.strip()
    if not path or "\x00" in path or len(path) > COOKIE_PATH_MAX_CHARS:
        return ""
    return path


def _is_existing_regular_file(path_text: str) -> bool:
    try:
        return Path(path_text).is_file()
    except (OSError, ValueError, OverflowError):
        return False


def _save_app_settings(settings: dict) -> bool:
    settings_data = dict(settings)
    settings_data.pop(API_KEY_PLAINTEXT_FIELD, None)
    settings_data.pop(API_KEY_REMEMBER_FIELD, None)
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
                json.dump(settings_data, temp_file, ensure_ascii=False, indent=2)
                temp_file.write("\n")
            temp_path.replace(app_settings_file())
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        return True
    except (OSError, TypeError):
        return False


def _secure_api_key_storage_available() -> bool:
    if os.name != "nt":
        return False
    _load_dpapi_libraries()
    return True


def _protect_api_key_bytes(api_key_bytes: bytes) -> bytes:
    if not api_key_bytes:
        raise ValueError("empty API key")
    crypt32, kernel32 = _load_dpapi_libraries()
    input_buffer = ctypes.create_string_buffer(api_key_bytes)
    entropy_buffer = ctypes.create_string_buffer(API_KEY_OPTIONAL_ENTROPY)
    input_blob = _blob_from_buffer(input_buffer, len(api_key_bytes))
    entropy_blob = _blob_from_buffer(entropy_buffer, len(API_KEY_OPTIONAL_ENTROPY))
    output_blob = _DATA_BLOB()

    ok = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _unprotect_api_key_bytes(protected_bytes: bytes) -> bytes:
    if not protected_bytes:
        raise ValueError("empty protected payload")
    crypt32, kernel32 = _load_dpapi_libraries()
    input_buffer = ctypes.create_string_buffer(protected_bytes)
    entropy_buffer = ctypes.create_string_buffer(API_KEY_OPTIONAL_ENTROPY)
    input_blob = _blob_from_buffer(input_buffer, len(protected_bytes))
    entropy_blob = _blob_from_buffer(entropy_buffer, len(API_KEY_OPTIONAL_ENTROPY))
    output_blob = _DATA_BLOB()
    description = ctypes.c_wchar_p()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        ctypes.byref(description),
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(output_blob.pbData)
        if description:
            kernel32.LocalFree(description)


def _blob_from_buffer(buffer: ctypes.Array, size: int) -> _DATA_BLOB:
    return _DATA_BLOB(size, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))


def _load_dpapi_libraries():
    if os.name != "nt":
        raise OSError("Windows DPAPI is unavailable on this platform")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    return crypt32, kernel32
