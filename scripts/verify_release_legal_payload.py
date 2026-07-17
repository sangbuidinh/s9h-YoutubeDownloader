from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


CONTRACT_PATH = "legal/release-assets-v2.json"
PAYLOAD_FORMAT = "s9h-release-legal-payload-v1"
BUNDLE_FORMAT = "s9h-release-bundle-v2"
MANIFEST_PATH = "legal/LEGAL_MANIFEST.json"
RELEASE_NOTES_NAME = "RELEASE_NOTES.md"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FIXED_FILE_MODE = 0o100644 << 16
TAG_PATTERN = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$"
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LOCAL_PATH_PATTERN = re.compile(
    r"(?i)(?:(?<![a-z])[a-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|/(?:users|home|tmp)/)"
)
SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:SID|SAPISID|HSID)=[^;\s]+"),
    re.compile(r"https?://[^\s]+googlevideo\.com[^\s]*", re.IGNORECASE),
)
SOURCE_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tar.xz",
    ".7z",
    ".rar",
)
BINARY_SUFFIXES = (".exe", ".dll", ".pyd")
LEGAL_PAYLOAD_FILES = (
    "THIRD_PARTY_NOTICES.md",
    "legal/README.md",
    "legal/built-artifact-inventory.json",
    "legal/components.json",
    "legal/release-policy.json",
    "legal/source-correspondence.json",
    "legal/source-kit-requirements.json",
    "legal/licenses/Apache-2.0.txt",
    "legal/licenses/Deno-2.7.14-MIT.txt",
    "legal/licenses/FFmpeg-8.1.2-GPLv3.txt",
    "legal/licenses/PyInstaller-6.21.0-COPYING.txt",
    "legal/licenses/Python-3.11.9-LICENSE.txt",
    "legal/licenses/Tcl-Tk-license.terms",
    "legal/licenses/aria2-1.37.0-GPLv2.txt",
    "legal/licenses/yt-dlp-2026.03.17-UNLICENSE.txt",
)
PAYLOAD_PATHS = (
    "legal/THIRD_PARTY_NOTICES.md",
    "legal/materials/README.md",
    "legal/materials/built-artifact-inventory.json",
    "legal/materials/components.json",
    "legal/materials/release-policy.json",
    "legal/materials/source-correspondence.json",
    "legal/materials/source-kit-requirements.json",
    "legal/licenses/Apache-2.0.txt",
    "legal/licenses/Deno-2.7.14-MIT.txt",
    "legal/licenses/FFmpeg-8.1.2-GPLv3.txt",
    "legal/licenses/PyInstaller-6.21.0-COPYING.txt",
    "legal/licenses/Python-3.11.9-LICENSE.txt",
    "legal/licenses/Tcl-Tk-license.terms",
    "legal/licenses/aria2-1.37.0-GPLv2.txt",
    "legal/licenses/yt-dlp-2026.03.17-UNLICENSE.txt",
)
SOURCE_TEMPLATES = (
    {
        "id": "aria2",
        "filename": "Youtube-Downloaderbs-{tag}-aria2-source.zip",
        "status": "not-ready",
    },
    {
        "id": "ffmpeg",
        "filename": "Youtube-Downloaderbs-{tag}-ffmpeg-source.zip",
        "status": "not-ready",
    },
)
RELEASE_BLOCKERS = (
    "aria2-source-kit-not-ready",
    "ffmpeg-source-kit-not-ready",
    "source-assets-not-integrated",
    "release-policy-fail-closed",
)


class LegalPayloadError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a release legal payload")
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--portable-zip", required=True, type=Path)
    parser.add_argument("--legal-zip", required=True, type=Path)
    parser.add_argument("--release-notes", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--control-commit", required=True)
    args = parser.parse_args()
    try:
        verify_release_legal_payload(
            control_root=args.control_root,
            portable_zip=args.portable_zip,
            legal_zip=args.legal_zip,
            release_notes=args.release_notes,
            tag=args.tag,
            source_commit=args.source_commit,
            control_commit=args.control_commit,
        )
    except (LegalPayloadError, OSError, UnicodeError, zipfile.BadZipFile) as exc:
        print(f"release legal payload verification failed: {exc}", file=sys.stderr)
        return 1
    print("release legal payload verified")
    return 0


def verify_release_legal_payload(
    *,
    control_root: Path,
    portable_zip: Path,
    legal_zip: Path,
    release_notes: Path,
    tag: str,
    source_commit: str,
    control_commit: str,
) -> dict[str, Any]:
    validate_identity(tag, source_commit, control_commit)
    control_root = require_regular_directory(control_root, "control root")
    contract = load_asset_contract(control_root / CONTRACT_PATH)
    expected = expected_payload_bytes(control_root)
    verify_release_notes_checksum(release_notes, portable_zip)
    legal_entries = read_zip_entries(legal_zip, "legal payload ZIP", deterministic=True)
    portable_entries = read_zip_entries(portable_zip, "portable ZIP", deterministic=True)

    expected_names = set(expected) | {MANIFEST_PATH}
    if set(legal_entries) != expected_names:
        raise LegalPayloadError("legal payload ZIP file set is not exact")
    portable_legal = {
        name: data for name, data in portable_entries.items() if name.casefold().startswith("legal/")
    }
    if set(portable_legal) != expected_names:
        raise LegalPayloadError("portable ZIP legal file set is not exact")
    if not any(not name.casefold().startswith("legal/") for name in portable_entries):
        raise LegalPayloadError("portable ZIP contains no application payload")
    if portable_legal != legal_entries:
        raise LegalPayloadError("portable and companion legal payload bytes differ")

    for name, data in expected.items():
        if legal_entries[name] != data:
            raise LegalPayloadError(f"legal payload byte mismatch: {name}")
    manifest = _load_manifest(legal_entries[MANIFEST_PATH])
    _verify_manifest(
        manifest,
        expected=expected,
        tag=tag,
        source_commit=source_commit,
        control_commit=control_commit,
    )
    _verify_legal_paths(expected_names)
    _require(contract["legal_payload_files"] == list(LEGAL_PAYLOAD_FILES), "legal payload contract files changed")
    return manifest


def verify_release_notes_checksum(release_notes: Path, portable_zip: Path) -> str:
    portable_zip = portable_zip.expanduser().resolve(strict=False)
    release_notes = require_release_notes_path(release_notes, portable_zip)
    recorded, _, _ = parse_release_notes_checksum(
        release_notes.read_bytes(),
        portable_zip.name,
    )
    actual = sha256_file(portable_zip)
    if recorded != actual:
        raise LegalPayloadError("release notes portable ZIP checksum does not match")
    return recorded


def require_release_notes_path(release_notes: Path, portable_zip: Path) -> Path:
    portable_zip = portable_zip.expanduser().resolve(strict=False)
    release_root = portable_zip.parent.parent.resolve(strict=False)
    expected = release_root / RELEASE_NOTES_NAME
    candidate = Path(os.path.abspath(release_notes.expanduser()))
    if candidate != expected:
        raise LegalPayloadError("release notes path is outside the allowed release root")
    if _is_reparse(release_root) or _is_reparse(candidate):
        raise LegalPayloadError("release notes uses a reparse point")
    if not candidate.is_file():
        raise LegalPayloadError("release notes is unavailable")
    return candidate


def parse_release_notes_checksum(raw: bytes, portable_name: str) -> tuple[str, int, int]:
    if not isinstance(portable_name, str) or Path(portable_name).name != portable_name:
        raise LegalPayloadError("portable ZIP filename is invalid")
    text, content, content_offset = _validate_release_notes_bytes(raw)
    _verify_text_hygiene(text, "release notes")

    filename = portable_name.encode("ascii")
    filename_pattern = re.compile(
        rb"(?<![0-9A-Za-z_.-])" + re.escape(filename) + rb"(?![0-9A-Za-z_.-])"
    )
    hash_pattern = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")
    candidates: list[tuple[int, bytes]] = []
    offset = 0
    for line in content.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        filename_matches = list(filename_pattern.finditer(body))
        if filename_matches:
            if len(filename_matches) != 1:
                raise LegalPayloadError("release notes portable checksum line is malformed")
            candidates.append((offset, body))
        offset += len(line)

    if not candidates:
        raise LegalPayloadError("release notes portable checksum is missing")
    if len(candidates) != 1:
        raise LegalPayloadError("release notes portable checksum is duplicated")
    line_offset, line = candidates[0]
    hash_matches = list(hash_pattern.finditer(line))
    if len(hash_matches) != 1:
        raise LegalPayloadError("release notes portable checksum line is malformed")
    checksum = hash_matches[0].group(0).decode("ascii").lower()
    start = content_offset + line_offset + hash_matches[0].start()
    end = content_offset + line_offset + hash_matches[0].end()
    return checksum, start, end


def replace_release_notes_checksum(
    raw: bytes,
    portable_name: str,
    expected_checksum: str,
    replacement_checksum: str,
) -> bytes:
    for value, label in (
        (expected_checksum, "expected release notes checksum"),
        (replacement_checksum, "replacement release notes checksum"),
    ):
        if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value.lower()):
            raise LegalPayloadError(f"{label} is invalid")
    recorded, start, end = parse_release_notes_checksum(raw, portable_name)
    if recorded != expected_checksum.lower():
        raise LegalPayloadError("release notes checksum does not match the original portable ZIP")
    updated = raw[:start] + replacement_checksum.lower().encode("ascii") + raw[end:]
    final, _, _ = parse_release_notes_checksum(updated, portable_name)
    if final != replacement_checksum.lower():
        raise LegalPayloadError("release notes checksum update failed")
    return updated


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_asset_contract(path: Path) -> dict[str, Any]:
    raw = _read_canonical_text_file(path, CONTRACT_PATH)
    contract = _load_json(raw, "release asset contract")
    if not isinstance(contract, dict):
        raise LegalPayloadError("release asset contract must be an object")
    expected = {
        "schema_version": 1,
        "bundle_format": BUNDLE_FORMAT,
        "legal_payload_format": PAYLOAD_FORMAT,
        "release_readiness": "blocked",
        "legal_compliance_certified": False,
        "source_availability_certified": False,
        "source_kits_ready": False,
        "portable_legal_root": "legal",
        "legal_payload_asset_template": "Youtube-Downloaderbs-{tag}-legal.zip",
        "required_source_asset_templates": [dict(item) for item in SOURCE_TEMPLATES],
        "legal_payload_files": list(LEGAL_PAYLOAD_FILES),
        "release_blockers": list(RELEASE_BLOCKERS),
    }
    if contract != expected:
        raise LegalPayloadError("release asset contract is invalid")
    canonical = (json.dumps(expected, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
    if raw != canonical:
        raise LegalPayloadError("release asset contract is not canonical")
    _verify_text_hygiene(raw.decode("utf-8"), CONTRACT_PATH)
    return contract


def expected_payload_bytes(control_root: Path) -> dict[str, bytes]:
    mapping = dict(zip(LEGAL_PAYLOAD_FILES, PAYLOAD_PATHS, strict=True))
    result: dict[str, bytes] = {}
    for source_name, payload_name in mapping.items():
        source = control_root / source_name
        require_regular_file(source, control_root, source_name)
        data = source.read_bytes()
        if not data:
            raise LegalPayloadError(f"legal payload input is empty: {source_name}")
        result[payload_name] = data
    return dict(sorted(result.items()))


def build_manifest_bytes(
    *,
    payload: dict[str, bytes],
    tag: str,
    source_commit: str,
    control_commit: str,
) -> bytes:
    validate_identity(tag, source_commit, control_commit)
    records = [
        {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(payload.items())
    ]
    manifest = {
        "schema_version": 1,
        "payload_format": PAYLOAD_FORMAT,
        "release_tag": tag,
        "source_commit": source_commit,
        "control_commit": control_commit,
        "project_license_status": "not-selected",
        "legal_compliance_certified": False,
        "source_availability_certified": False,
        "source_kits_ready": False,
        "files": records,
    }
    return (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def read_zip_entries(path: Path, label: str, *, deterministic: bool) -> dict[str, bytes]:
    require_regular_file(path, path.parent.resolve(strict=False), label)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise LegalPayloadError(f"{label} contains duplicate entries")
            if len(names) != len({name.casefold() for name in names}):
                raise LegalPayloadError(f"{label} contains a case-insensitive collision")
            entries: dict[str, bytes] = {}
            for info in infos:
                _validate_zip_info(info, label, deterministic=deterministic)
                if info.is_dir():
                    continue
                entries[info.filename] = archive.read(info)
            if archive.testzip() is not None:
                raise LegalPayloadError(f"{label} contains corrupt data")
    except (OSError, zipfile.BadZipFile) as exc:
        raise LegalPayloadError(f"{label} is not a readable ZIP") from exc
    if not entries:
        raise LegalPayloadError(f"{label} is empty")
    return entries


def validate_identity(tag: str, source_commit: str, control_commit: str) -> None:
    if not isinstance(tag, str) or not TAG_PATTERN.fullmatch(tag):
        raise LegalPayloadError("release tag is invalid")
    if not isinstance(source_commit, str) or not COMMIT_PATTERN.fullmatch(source_commit):
        raise LegalPayloadError("source commit is invalid")
    if not isinstance(control_commit, str) or not COMMIT_PATTERN.fullmatch(control_commit):
        raise LegalPayloadError("control commit is invalid")


def require_regular_directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if not resolved.is_dir() or _is_reparse(resolved):
        raise LegalPayloadError(f"{label} is unavailable")
    return resolved


def require_regular_file(path: Path, root: Path, label: str) -> None:
    resolved = path.resolve(strict=False)
    root = root.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise LegalPayloadError(f"{label} escapes its allowed root") from exc
    current = root
    if _is_reparse(current):
        raise LegalPayloadError(f"{label} uses a reparse point")
    for part in resolved.relative_to(root).parts:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise LegalPayloadError(f"{label} uses a reparse point")
    if not resolved.is_file():
        raise LegalPayloadError(f"{label} is unavailable")


def _verify_manifest(
    manifest: object,
    *,
    expected: dict[str, bytes],
    tag: str,
    source_commit: str,
    control_commit: str,
) -> None:
    if not isinstance(manifest, dict):
        raise LegalPayloadError("legal manifest must be an object")
    _require_exact_keys(
        manifest,
        {
            "schema_version",
            "payload_format",
            "release_tag",
            "source_commit",
            "control_commit",
            "project_license_status",
            "legal_compliance_certified",
            "source_availability_certified",
            "source_kits_ready",
            "files",
        },
        "legal manifest",
    )
    checks = (
        (manifest["schema_version"] == 1, "legal manifest schema is invalid"),
        (manifest["payload_format"] == PAYLOAD_FORMAT, "legal manifest format is invalid"),
        (manifest["release_tag"] == tag, "legal manifest tag is invalid"),
        (manifest["source_commit"] == source_commit, "legal manifest source commit is invalid"),
        (manifest["control_commit"] == control_commit, "legal manifest control commit is invalid"),
        (manifest["project_license_status"] == "not-selected", "project license status changed"),
        (manifest["legal_compliance_certified"] is False, "legal compliance was certified"),
        (manifest["source_availability_certified"] is False, "source availability was certified"),
        (manifest["source_kits_ready"] is False, "source kits were marked ready"),
    )
    for condition, message in checks:
        _require(condition, message)
    records = manifest["files"]
    if not isinstance(records, list) or len(records) != len(expected):
        raise LegalPayloadError("legal manifest file records are invalid")
    names: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise LegalPayloadError("legal manifest file record is invalid")
        _require_exact_keys(record, {"name", "size", "sha256"}, "legal manifest file record")
        name = record["name"]
        if not isinstance(name, str) or name not in expected:
            raise LegalPayloadError("legal manifest filename is invalid")
        if type(record["size"]) is not int or record["size"] <= 0:
            raise LegalPayloadError("legal manifest file size is invalid")
        if not isinstance(record["sha256"], str) or not SHA256_PATTERN.fullmatch(record["sha256"]):
            raise LegalPayloadError("legal manifest file SHA-256 is invalid")
        data = expected[name]
        if record["size"] != len(data) or record["sha256"] != hashlib.sha256(data).hexdigest():
            raise LegalPayloadError(f"legal manifest record does not match: {name}")
        names.append(name)
    if names != sorted(expected) or len(names) != len(set(names)):
        raise LegalPayloadError("legal manifest files are not sorted and unique")


def _load_manifest(raw: bytes) -> object:
    _require_canonical_text(raw, "legal manifest")
    manifest = _load_json(raw, "legal manifest")
    canonical = (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
    if raw != canonical:
        raise LegalPayloadError("legal manifest is not canonical")
    _verify_text_hygiene(raw.decode("utf-8"), "legal manifest")
    return manifest


def _validate_release_notes_bytes(raw: bytes) -> tuple[str, bytes, int]:
    bom = b"\xef\xbb\xbf"
    content_offset = len(bom) if raw.startswith(bom) else 0
    content = raw[content_offset:]
    if not content or b"\0" in content:
        raise LegalPayloadError("release notes content is invalid")

    if b"\r\n" in content:
        without_crlf = content.replace(b"\r\n", b"")
        if b"\r" in without_crlf or b"\n" in without_crlf:
            raise LegalPayloadError("release notes line endings are mixed")
        newline = b"\r\n"
    else:
        if b"\r" in content:
            raise LegalPayloadError("release notes contain a bare CR")
        newline = b"\n"
    if not content.endswith(newline) or content.endswith(newline + newline):
        raise LegalPayloadError("release notes must have exactly one final newline")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LegalPayloadError("release notes are not valid UTF-8") from exc
    return text, content, content_offset


def _read_canonical_text_file(path: Path, label: str) -> bytes:
    require_regular_file(path, path.parent.parent.resolve(strict=False), label)
    raw = path.read_bytes()
    _require_canonical_text(raw, label)
    return raw


def _require_canonical_text(raw: bytes, label: str) -> None:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise LegalPayloadError(f"{label} contains a BOM")
    if b"\0" in raw:
        raise LegalPayloadError(f"{label} contains NUL")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise LegalPayloadError(f"{label} must use LF with one final newline")
    raw.decode("utf-8")


def _load_json(raw: bytes, label: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise LegalPayloadError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LegalPayloadError(f"{label} JSON is invalid") from exc


def _validate_zip_info(info: zipfile.ZipInfo, label: str, *, deterministic: bool) -> None:
    name = info.filename
    if not name or "\\" in name or "\0" in name:
        raise LegalPayloadError(f"{label} contains an unsafe ZIP path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise LegalPayloadError(f"{label} contains an unsafe ZIP path")
    if ":" in path.parts[0]:
        raise LegalPayloadError(f"{label} contains an absolute ZIP path")
    if info.flag_bits & 0x1:
        raise LegalPayloadError(f"{label} contains an encrypted entry")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise LegalPayloadError(f"{label} contains a symbolic link")
    if deterministic:
        expected_mode = (0o40755 << 16) if info.is_dir() else FIXED_FILE_MODE
        if (
            info.date_time != FIXED_ZIP_TIMESTAMP
            or info.compress_type != zipfile.ZIP_DEFLATED
            or info.create_system != 3
            or info.external_attr != expected_mode
        ):
            raise LegalPayloadError(f"{label} ZIP metadata is not deterministic")


def _verify_legal_paths(names: set[str]) -> None:
    license_names = {name for name in names if name.startswith("legal/licenses/")}
    expected_licenses = {name for name in PAYLOAD_PATHS if name.startswith("legal/licenses/")}
    if license_names != expected_licenses or len(license_names) != 8:
        raise LegalPayloadError("legal payload license set is not exact")
    for name in names:
        folded = name.casefold()
        if folded.endswith(BINARY_SUFFIXES):
            raise LegalPayloadError("binary file is not allowed below legal paths")
        if folded.endswith(SOURCE_ARCHIVE_SUFFIXES):
            raise LegalPayloadError("source archive is not allowed in the legal payload")


def _verify_text_hygiene(text: str, label: str) -> None:
    if LOCAL_PATH_PATTERN.search(text):
        raise LegalPayloadError(f"local absolute path in {label}")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise LegalPayloadError(f"secret-like value in {label}")


def _require_exact_keys(value: dict, expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise LegalPayloadError(f"{label} fields are invalid")


def _is_reparse(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return path.is_symlink()
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LegalPayloadError(message)


if __name__ == "__main__":
    raise SystemExit(main())
