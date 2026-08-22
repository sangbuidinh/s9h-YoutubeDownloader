"""Cross-process ownership for channel namespaces and final media paths."""

import ctypes
import hashlib
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping

from core.download_contracts import DownloadError
from core.download_modes import PART_AUDIO, PART_THUMB, PART_VIDEO


CHANNEL_OWNER_FILENAME = ".s9h-channel-owner.json"
OUTPUT_OWNERS_FILENAME = ".s9h-output-owners.json"
OWNERSHIP_SCHEMA_VERSION = 1
MUTEX_TIMEOUT_SECONDS = 30.0
_SUPPORTED_PARTS = (PART_VIDEO, PART_THUMB, PART_AUDIO)


class OutputOwnershipError(DownloadError):
    """Raised when an output namespace belongs to another logical item."""


@dataclass(frozen=True)
class OutputClaim:
    channel_dir: Path
    final_path: Path
    channel_id: str
    video_id: str
    part: str
    relative_path: str
    registry_key: str


@dataclass(frozen=True)
class OutputReservation:
    channel_dir: Path
    channel_id: str
    video_id: str
    claims: tuple[OutputClaim, ...]

    def claim_for_path(self, final_path: str | Path) -> OutputClaim:
        requested = canonical_path_key(final_path)
        for claim in self.claims:
            if canonical_path_key(claim.final_path) == requested:
                return claim
        raise OutputOwnershipError("Output path was not reserved for the current video.")


def canonical_path_key(path: str | Path) -> str:
    resolved = str(Path(path).expanduser().resolve(strict=False))
    normalized = os.path.normpath(resolved)
    if os.name == "nt":
        normalized = os.path.normcase(normalized)
    return normalized.replace("\\", "/")


def resolve_channel_directory(
    base_folder: str | Path,
    channel_name: str,
    channel_id: str,
    sanitized_channel_name: str,
    *,
    legacy_channel_ids: Callable[[Path], set[str]] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> Path:
    if not str(channel_id or "").strip():
        raise OutputOwnershipError("Channel identity is required for output ownership.")

    base = Path(base_folder)
    legacy_dir = base / sanitized_channel_name
    namespace_resource = base / f".s9h-channel-namespace-{sanitized_channel_name}"
    with named_mutex(namespace_resource, cancel_check=cancel_check):
        if _directory_is_compatible(
            legacy_dir,
            channel_id,
            channel_name,
            legacy_channel_ids=legacy_channel_ids,
        ):
            _claim_channel_directory(legacy_dir, channel_id, channel_name)
            return legacy_dir

        digest = hashlib.sha256(channel_id.encode("utf-8")).hexdigest()
        for suffix_length in range(12, 65, 4):
            candidate = base / f"{sanitized_channel_name} [{digest[:suffix_length]}]"
            if not _directory_is_compatible(
                candidate,
                channel_id,
                channel_name,
                legacy_channel_ids=legacy_channel_ids,
            ):
                continue
            _claim_channel_directory(candidate, channel_id, channel_name)
            return candidate

    raise OutputOwnershipError("Could not resolve a deterministic channel output namespace.")


def reserve_output_paths(
    channel_dir: str | Path,
    channel_id: str,
    video_id: str,
    part_paths: Mapping[str, str | Path],
    *,
    legacy_owner_lookup: Callable[[tuple[Path, ...]], Mapping[str, set[tuple[str, str, str]]]] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> OutputReservation:
    normalized_channel_id = str(channel_id or "").strip()
    normalized_video_id = str(video_id or "").strip()
    if not normalized_channel_id or not normalized_video_id:
        raise OutputOwnershipError("Channel and video identity are required for output ownership.")

    root = Path(channel_dir)
    claims = tuple(
        _make_claim(root, normalized_channel_id, normalized_video_id, part, Path(path))
        for part, path in part_paths.items()
    )
    if not claims:
        raise OutputOwnershipError("At least one output path must be reserved.")
    if len({claim.registry_key for claim in claims}) != len(claims):
        raise OutputOwnershipError("Multiple media parts resolved to the same output path.")

    registry_path = root / OUTPUT_OWNERS_FILENAME
    with named_mutex(registry_path, cancel_check=cancel_check):
        document = _read_output_registry(registry_path)
        owners = document["owners"]
        unresolved_claims = tuple(
            claim.final_path
            for claim in claims
            if claim.registry_key not in owners
        )
        legacy_owners: Mapping[str, set[tuple[str, str, str]]] = {}
        if unresolved_claims and legacy_owner_lookup is not None:
            legacy_owners = legacy_owner_lookup(unresolved_claims)

        additions: dict[str, dict] = {}
        for claim in claims:
            existing_owner = owners.get(claim.registry_key)
            if existing_owner is not None:
                _require_compatible_owner(claim, existing_owner)
                continue

            state_owners = legacy_owners.get(canonical_path_key(claim.final_path), set())
            expected_owner = (claim.channel_id, claim.video_id, claim.part)
            if state_owners and state_owners != {expected_owner}:
                raise _collision_error(claim, "SQLite state proves another logical owner")
            if _file_exists(claim.final_path) and state_owners != {expected_owner}:
                raise _collision_error(claim, "existing media has no single proven matching owner")

            additions[claim.registry_key] = _owner_document(claim)

        if additions:
            owners.update(additions)
            _write_json_atomic(registry_path, document)

    return OutputReservation(root, normalized_channel_id, normalized_video_id, claims)


@contextmanager
def verify_output_claim(
    claim: OutputClaim,
    *,
    cancel_check: Callable[[], None] | None = None,
) -> Iterator[None]:
    registry_path = claim.channel_dir / OUTPUT_OWNERS_FILENAME
    with named_mutex(registry_path, cancel_check=cancel_check):
        document = _read_output_registry(registry_path)
        existing_owner = document["owners"].get(claim.registry_key)
        if existing_owner is None:
            raise _collision_error(claim, "ownership reservation is missing")
        _require_compatible_owner(claim, existing_owner)
        yield


@contextmanager
def named_mutex(
    resource: str | Path,
    *,
    timeout_seconds: float = MUTEX_TIMEOUT_SECONDS,
    cancel_check: Callable[[], None] | None = None,
) -> Iterator[None]:
    digest = hashlib.sha256(canonical_path_key(resource).encode("utf-8")).hexdigest()
    if os.name == "nt":
        with _windows_named_mutex(digest, timeout_seconds, cancel_check):
            yield
        return

    with _posix_advisory_lock(digest, timeout_seconds, cancel_check):
        yield


def _directory_is_compatible(
    channel_dir: Path,
    channel_id: str,
    channel_name: str,
    *,
    legacy_channel_ids: Callable[[Path], set[str]] | None,
) -> bool:
    marker_path = channel_dir / CHANNEL_OWNER_FILENAME
    if marker_path.exists():
        marker = _read_channel_owner(marker_path)
        return marker["channel_id"] == channel_id

    if not channel_dir.exists():
        return True
    if not channel_dir.is_dir():
        return False
    if not _contains_historical_media(channel_dir):
        return True

    known_ids = legacy_channel_ids(channel_dir) if legacy_channel_ids is not None else set()
    return known_ids == {channel_id}


def _claim_channel_directory(channel_dir: Path, channel_id: str, channel_name: str) -> None:
    channel_dir.mkdir(parents=True, exist_ok=True)
    marker_path = channel_dir / CHANNEL_OWNER_FILENAME
    if marker_path.exists():
        marker = _read_channel_owner(marker_path)
        if marker["channel_id"] != channel_id:
            raise OutputOwnershipError("Channel output namespace is owned by another channel.")
        return
    _write_json_atomic(
        marker_path,
        {
            "schema_version": OWNERSHIP_SCHEMA_VERSION,
            "channel_id": channel_id,
            "channel_name": str(channel_name or ""),
        },
    )


def _contains_historical_media(channel_dir: Path) -> bool:
    for child_name in ("video", "thumb", "audio"):
        child = channel_dir / child_name
        if not child.is_dir():
            continue
        try:
            if any(path.is_file() for path in child.iterdir()):
                return True
        except OSError:
            return True
    return False


def _make_claim(
    channel_dir: Path,
    channel_id: str,
    video_id: str,
    part: str,
    final_path: Path,
) -> OutputClaim:
    if part not in _SUPPORTED_PARTS:
        raise OutputOwnershipError("Unsupported output part ownership request.")
    root = channel_dir.resolve(strict=False)
    resolved_final = final_path.resolve(strict=False)
    try:
        relative = resolved_final.relative_to(root)
    except ValueError as exc:
        raise OutputOwnershipError("Output path escaped the channel directory.") from exc
    relative_text = relative.as_posix()
    if relative.is_absolute() or not relative.parts or any(part_value in ("", ".", "..") for part_value in relative.parts):
        raise OutputOwnershipError("Output path is not a safe channel-relative path.")
    registry_key = relative_text.casefold() if os.name == "nt" else relative_text
    return OutputClaim(
        channel_dir=root,
        final_path=resolved_final,
        channel_id=channel_id,
        video_id=video_id,
        part=part,
        relative_path=relative_text,
        registry_key=registry_key,
    )


def _owner_document(claim: OutputClaim) -> dict:
    return {
        "channel_id": claim.channel_id,
        "video_id": claim.video_id,
        "part": claim.part,
        "relative_path": claim.relative_path,
    }


def _require_compatible_owner(claim: OutputClaim, owner: object) -> None:
    if not isinstance(owner, dict):
        raise OutputOwnershipError("Output ownership registry contains an invalid owner record.")
    actual = (owner.get("channel_id"), owner.get("video_id"), owner.get("part"))
    expected = (claim.channel_id, claim.video_id, claim.part)
    if actual != expected:
        raise _collision_error(claim, "the filename is reserved for another logical video")


def _collision_error(claim: OutputClaim, reason: str) -> OutputOwnershipError:
    return OutputOwnershipError(f"Output filename collision: {claim.final_path.stem} ({reason}).")


def _read_channel_owner(path: Path) -> dict:
    document = _read_json_document(path)
    if set(document) != {"schema_version", "channel_id", "channel_name"}:
        raise OutputOwnershipError("Channel ownership metadata has an unsupported shape.")
    if document.get("schema_version") != OWNERSHIP_SCHEMA_VERSION:
        raise OutputOwnershipError("Channel ownership metadata has an unsupported schema version.")
    if not isinstance(document.get("channel_id"), str) or not document["channel_id"]:
        raise OutputOwnershipError("Channel ownership metadata is missing channel identity.")
    if not isinstance(document.get("channel_name"), str):
        raise OutputOwnershipError("Channel ownership metadata contains an invalid channel name.")
    return document


def _read_output_registry(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": OWNERSHIP_SCHEMA_VERSION, "owners": {}}
    document = _read_json_document(path)
    if set(document) != {"schema_version", "owners"}:
        raise OutputOwnershipError("Output ownership registry has an unsupported shape.")
    if document.get("schema_version") != OWNERSHIP_SCHEMA_VERSION:
        raise OutputOwnershipError("Output ownership registry has an unsupported schema version.")
    owners = document.get("owners")
    if not isinstance(owners, dict):
        raise OutputOwnershipError("Output ownership registry is invalid.")
    for key, owner in owners.items():
        if not isinstance(key, str) or not key or key.startswith("/") or ".." in Path(key).parts:
            raise OutputOwnershipError("Output ownership registry contains an unsafe path key.")
        if not isinstance(owner, dict) or set(owner) != {"channel_id", "video_id", "part", "relative_path"}:
            raise OutputOwnershipError("Output ownership registry contains an invalid owner record.")
        if owner.get("part") not in _SUPPORTED_PARTS:
            raise OutputOwnershipError("Output ownership registry contains an invalid media part.")
        if any(not isinstance(owner.get(name), str) or not owner[name] for name in ("channel_id", "video_id", "relative_path")):
            raise OutputOwnershipError("Output ownership registry contains incomplete identity metadata.")
        expected_key = owner["relative_path"].casefold() if os.name == "nt" else owner["relative_path"]
        if key != expected_key:
            raise OutputOwnershipError("Output ownership registry path key is inconsistent.")
    return document


def _read_json_document(path: Path) -> dict:
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("UTF-8 BOM is not allowed")
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise OutputOwnershipError(f"Could not read valid output ownership metadata: {path.name}.") from exc
    if not isinstance(document, dict):
        raise OutputOwnershipError(f"Output ownership metadata is not an object: {path.name}.")
    return document


def _write_json_atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.tmp-",
            suffix=".json",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError as exc:
        raise OutputOwnershipError(f"Could not update output ownership metadata: {path.name}.") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _file_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return True


@contextmanager
def _windows_named_mutex(
    digest: str,
    timeout_seconds: float,
    cancel_check: Callable[[], None] | None,
) -> Iterator[None]:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for_single_object.restype = wintypes.DWORD
    release_mutex = kernel32.ReleaseMutex
    release_mutex.argtypes = (wintypes.HANDLE,)
    release_mutex.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = create_mutex(None, False, f"Local\\s9h-ytdl-output-{digest}")
    if not handle:
        raise OutputOwnershipError(f"Could not create output ownership mutex ({ctypes.get_last_error()}).")

    acquired = False
    started = time.monotonic()
    try:
        while True:
            if cancel_check is not None:
                cancel_check()
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise OutputOwnershipError("Timed out waiting for output ownership.")
            wait_milliseconds = max(1, min(100, math.ceil(remaining * 1000)))
            result = int(wait_for_single_object(handle, wait_milliseconds))
            if result in (0x00000000, 0x00000080):
                acquired = True
                break
            if result == 0x00000102:
                continue
            if result == 0xFFFFFFFF:
                raise OutputOwnershipError(f"Could not wait for output ownership mutex ({ctypes.get_last_error()}).")
            raise OutputOwnershipError(f"Unexpected output ownership mutex result: {result}.")
        yield
    finally:
        if acquired:
            release_mutex(handle)
        close_handle(handle)


@contextmanager
def _posix_advisory_lock(
    digest: str,
    timeout_seconds: float,
    cancel_check: Callable[[], None] | None,
) -> Iterator[None]:
    import errno
    import fcntl

    lock_path = Path(tempfile.gettempdir()) / f"s9h-ytdl-output-{digest}.lock"
    with lock_path.open("a+b") as handle:
        started = time.monotonic()
        while True:
            if cancel_check is not None:
                cancel_check()
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise OutputOwnershipError("Could not acquire output ownership lock.") from exc
                if time.monotonic() - started >= timeout_seconds:
                    raise OutputOwnershipError("Timed out waiting for output ownership.") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
