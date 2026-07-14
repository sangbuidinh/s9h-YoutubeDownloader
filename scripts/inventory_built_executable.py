from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import stat
import struct
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


BASELINE_COMMIT = "988c07f9d3e099b3ff157e33d880c0bad73ad112"
EXPECTED_EXE_NAME = "Youtube Downloaderbs.exe"
TOP_LEVEL_KEYS = (
    "schema_version",
    "inventory_kind",
    "source_commit",
    "target_platform",
    "python_version",
    "pyinstaller_version",
    "executable",
    "archive",
    "native_members",
    "detected_components",
    "unresolved_native_members",
)
EXECUTABLE_KEYS = ("name", "size", "sha256")
ARCHIVE_KEYS = ("member_count", "canonical_member_list_sha256", "native_member_count")
NATIVE_KEYS = (
    "name",
    "size",
    "sha256",
    "kind",
    "version_evidence",
    "license_mapping",
    "status",
)
COMPONENT_KEYS = ("id", "name", "version", "evidence", "license_files", "status")
NATIVE_KINDS = {"dll", "pyd", "exe", "other-native"}
STATUSES = {"identified", "partially-identified", "unresolved"}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
LOCAL_PATH_RE = re.compile(r"(?i)(?:(?<![a-z])[a-z]:[\\/]|\\\\[^\\\s]+\\[^\\\s]+|/(?:users|home|tmp)/)")
SECRET_RES = (
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:SID|SAPISID|HSID)=[^;\s]+"),
    re.compile(r"https?://[^\s]+googlevideo\.com[^\s]*", re.IGNORECASE),
)
FORBIDDEN_CLAIMS = (
    "exhaustive inventory",
    "full binary inventory",
    "legal compliance",
    "gpl compliance",
    "source compliance",
)


class InventoryError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory a controlled PyInstaller one-file executable")
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--pyinstaller-version", required=True)
    args = parser.parse_args()
    try:
        inventory = build_inventory(
            args.exe,
            source_commit=args.source_commit,
            python_version=args.python_version,
            pyinstaller_version=args.pyinstaller_version,
        )
        write_inventory(args.output, inventory)
    except (InventoryError, OSError, UnicodeError, ValueError) as exc:
        print(f"executable inventory failed: {exc}", file=os.sys.stderr)
        return 1
    return 0


def build_inventory(
    executable: Path,
    *,
    source_commit: str,
    python_version: str,
    pyinstaller_version: str,
) -> dict[str, Any]:
    executable = Path(executable).resolve(strict=True)
    _verify_regular_file(executable)
    _verify_pe_x64(executable)
    _require(COMMIT_RE.fullmatch(source_commit) is not None, "source commit must be lowercase 40-hex")
    _require(re.fullmatch(r"\d+\.\d+\.\d+", python_version) is not None, "Python version is invalid")
    _require(re.fullmatch(r"\d+\.\d+\.\d+", pyinstaller_version) is not None, "PyInstaller version is invalid")

    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        raise InventoryError("locked PyInstaller reader is unavailable") from exc

    try:
        reader = CArchiveReader(str(executable))
    except Exception as exc:
        raise InventoryError("PyInstaller CArchive could not be opened") from exc

    raw_entries = _read_raw_toc(executable, reader, CArchiveReader)
    _require(set(raw_entries) == set(reader.toc), "CArchive reader and raw TOC differ")
    canonical_names: dict[str, str] = {}
    seen_canonical: set[str] = set()
    for original in raw_entries:
        canonical = canonical_member_name(original)
        _require(canonical not in seen_canonical, f"ambiguous canonical archive member: {canonical}")
        seen_canonical.add(canonical)
        canonical_names[original] = canonical

    sorted_members = sorted(canonical_names.values(), key=lambda value: (value.casefold(), value))
    member_list_bytes = ("\n".join(sorted_members) + "\n").encode("utf-8")
    native_originals = [
        original for original, canonical in canonical_names.items() if _native_kind(canonical) is not None
    ]
    native_records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="s9h-native-inventory-") as temporary:
        temporary_root = Path(temporary).resolve()
        for original in native_originals:
            canonical = canonical_names[original]
            try:
                data = reader.extract(original)
            except Exception as exc:
                raise InventoryError(f"CArchive member extraction failed: {canonical}") from exc
            _require(
                len(data) == raw_entries[original][2],
                f"CArchive member size differs from TOC: {canonical}",
            )
            target = temporary_root.joinpath(*PurePosixPath(canonical).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            _require(not target.exists(), f"duplicate extraction target: {canonical}")
            target.write_bytes(data)
            evidence, mapping, status = _classify_native(canonical, python_version)
            version = _pe_fixed_file_version(target)
            if version is not None:
                evidence.append(f"PE fixed file version {version}")
            native_records.append(
                {
                    "name": canonical,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "kind": _native_kind(canonical),
                    "version_evidence": sorted(set(evidence), key=str.casefold),
                    "license_mapping": mapping,
                    "status": status,
                }
            )

    native_records.sort(key=lambda record: (record["name"].casefold(), record["name"]))
    components = _component_records(native_records, python_version, pyinstaller_version)
    unresolved = sorted(
        [record["name"] for record in native_records if record["status"] == "unresolved"],
        key=lambda value: (value.casefold(), value),
    )
    inventory = {
        "schema_version": 1,
        "inventory_kind": "controlled-pyinstaller-onefile",
        "source_commit": source_commit,
        "target_platform": "windows-x86_64",
        "python_version": python_version,
        "pyinstaller_version": pyinstaller_version,
        "executable": {
            "name": executable.name,
            "size": executable.stat().st_size,
            "sha256": _sha256_file(executable),
        },
        "archive": {
            "member_count": len(sorted_members),
            "canonical_member_list_sha256": hashlib.sha256(member_list_bytes).hexdigest(),
            "native_member_count": len(native_records),
        },
        "native_members": native_records,
        "detected_components": components,
        "unresolved_native_members": unresolved,
    }
    validate_inventory_document(inventory)
    return inventory


def canonical_inventory_bytes(inventory: dict[str, Any]) -> bytes:
    return (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_inventory(output: Path, inventory: dict[str, Any]) -> None:
    validate_inventory_document(inventory)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        _verify_regular_file(output)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_bytes(canonical_inventory_bytes(inventory))
    os.replace(temporary, output)


def load_inventory(path: Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    _require(not raw.startswith(b"\xef\xbb\xbf"), "inventory contains a UTF-8 BOM")
    _require(b"\r" not in raw, "inventory must use LF line endings")
    _require(b"\0" not in raw, "inventory contains NUL")
    try:
        value = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise InventoryError("inventory JSON is malformed") from exc
    validate_inventory_document(value)
    _require(raw == canonical_inventory_bytes(value), "inventory JSON is not deterministic")
    return value


def validate_inventory_document(inventory: Any) -> dict[str, Any]:
    _require(isinstance(inventory, dict), "inventory root must be an object")
    _require(tuple(inventory) == TOP_LEVEL_KEYS, "inventory top-level schema or field order is invalid")
    _require(inventory["schema_version"] == 1, "inventory schema_version must be 1")
    _require(inventory["inventory_kind"] == "controlled-pyinstaller-onefile", "inventory kind is invalid")
    _require(inventory["source_commit"] == BASELINE_COMMIT, "inventory source commit is invalid")
    _require(inventory["target_platform"] == "windows-x86_64", "inventory target platform is invalid")
    _require(inventory["python_version"] == "3.11.9", "inventory Python version is invalid")
    _require(inventory["pyinstaller_version"] == "6.21.0", "inventory PyInstaller version is invalid")

    executable = inventory["executable"]
    _require(isinstance(executable, dict) and tuple(executable) == EXECUTABLE_KEYS, "executable schema is invalid")
    _require(executable["name"] == EXPECTED_EXE_NAME, "executable name is invalid")
    _require(_positive_int(executable["size"]), "executable size is invalid")
    _require(_valid_sha256(executable["sha256"]), "executable SHA-256 is invalid")

    archive = inventory["archive"]
    _require(isinstance(archive, dict) and tuple(archive) == ARCHIVE_KEYS, "archive schema is invalid")
    _require(_positive_int(archive["member_count"]), "archive member count is invalid")
    _require(_valid_sha256(archive["canonical_member_list_sha256"]), "canonical member-list digest is invalid")
    _require(_positive_int(archive["native_member_count"]), "native member count is invalid")

    native_members = inventory["native_members"]
    _require(isinstance(native_members, list) and native_members, "native member inventory is missing")
    names: list[str] = []
    for record in native_members:
        _require(isinstance(record, dict) and tuple(record) == NATIVE_KEYS, "native member schema is invalid")
        name = record["name"]
        _require(isinstance(name, str) and canonical_member_name(name) == name, "native member path is unsafe")
        _require(_positive_int(record["size"]), f"native member size is invalid: {name}")
        _require(_valid_sha256(record["sha256"]), f"native member SHA-256 is invalid: {name}")
        _require(record["kind"] in NATIVE_KINDS, f"native member kind is invalid: {name}")
        evidence = record["version_evidence"]
        _require(_string_list(evidence, sorted_required=True), f"native member evidence is invalid: {name}")
        _require(isinstance(record["license_mapping"], str) and record["license_mapping"], f"license mapping is invalid: {name}")
        _require(record["status"] in STATUSES, f"native member status is invalid: {name}")
        names.append(name)
    _require(len(names) == len(set(names)), "native member names must be unique")
    _require(names == sorted(names, key=lambda value: (value.casefold(), value)), "native members must be sorted")
    _require(archive["native_member_count"] == len(native_members), "native member count does not match")

    components = inventory["detected_components"]
    _require(isinstance(components, list) and components, "detected components are missing")
    component_ids: list[str] = []
    for record in components:
        _require(isinstance(record, dict) and tuple(record) == COMPONENT_KEYS, "component schema is invalid")
        component_id = record["id"]
        _require(isinstance(component_id, str) and re.fullmatch(r"[a-z0-9-]+", component_id), "component ID is invalid")
        _require(isinstance(record["name"], str) and record["name"], f"component name is invalid: {component_id}")
        _require(isinstance(record["version"], str) and record["version"], f"component version is invalid: {component_id}")
        _require(_string_list(record["evidence"], sorted_required=True), f"component evidence is invalid: {component_id}")
        _require(_string_list(record["license_files"], sorted_required=True, allow_empty=True), f"component licenses are invalid: {component_id}")
        for relative in record["license_files"]:
            _require(relative.startswith("legal/licenses/") and canonical_member_name(relative) == relative, "component license path is invalid")
        _require(record["status"] in STATUSES, f"component status is invalid: {component_id}")
        if record["version"] != "unverified":
            _require(bool(record["evidence"]), f"component version lacks evidence: {component_id}")
        component_ids.append(component_id)
    _require(len(component_ids) == len(set(component_ids)), "component IDs must be unique")
    _require(component_ids == sorted(component_ids), "detected components must be sorted")

    unresolved = inventory["unresolved_native_members"]
    _require(_string_list(unresolved, sorted_required=True, allow_empty=True), "unresolved member list is invalid")
    expected_unresolved = [record["name"] for record in native_members if record["status"] == "unresolved"]
    _require(unresolved == expected_unresolved, "unresolved native members are not explicit")
    _verify_hygiene(inventory)
    return inventory


def canonical_member_name(name: str) -> str:
    _require(isinstance(name, str) and name and "\0" not in name, "archive member name is invalid")
    _require(not PurePosixPath(name).is_absolute(), f"absolute archive member path: {name}")
    _require(not PureWindowsPath(name).is_absolute() and not re.match(r"^[A-Za-z]:", name), f"absolute archive member path: {name}")
    _require(not ("/" in name and "\\" in name), f"backslash ambiguity in archive member: {name}")
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    _require(all(part not in {"", ".", ".."} for part in parts), f"archive member traversal or ambiguity: {name}")
    return "/".join(parts)


def _read_raw_toc(executable: Path, reader: Any, reader_class: Any) -> dict[str, tuple[Any, ...]]:
    with executable.open("rb") as stream:
        cookie_offset = reader._find_magic_pattern(stream, reader_class._COOKIE_MAGIC_PATTERN)
        _require(cookie_offset >= 0, "PyInstaller cookie is missing")
        stream.seek(cookie_offset)
        cookie = stream.read(reader_class._COOKIE_LENGTH)
        _require(len(cookie) == reader_class._COOKIE_LENGTH, "PyInstaller cookie is truncated")
        _, archive_length, toc_offset, toc_length, _, _ = struct.unpack(reader_class._COOKIE_FORMAT, cookie)
        archive_start = cookie_offset + reader_class._COOKIE_LENGTH - archive_length
        stream.seek(archive_start + toc_offset)
        toc_data = stream.read(toc_length)
    _require(len(toc_data) == toc_length, "CArchive TOC is truncated")
    entries: dict[str, tuple[Any, ...]] = {}
    position = 0
    while position < len(toc_data):
        _require(position + reader_class._TOC_ENTRY_LENGTH <= len(toc_data), "CArchive TOC header is truncated")
        values = struct.unpack(
            reader_class._TOC_ENTRY_FORMAT,
            toc_data[position : position + reader_class._TOC_ENTRY_LENGTH],
        )
        entry_length, entry_offset, data_length, uncompressed_length, compression_flag, raw_type = values
        _require(entry_length >= reader_class._TOC_ENTRY_LENGTH, "CArchive TOC entry length is invalid")
        _require(
            entry_offset >= 0 and data_length >= 0 and uncompressed_length >= 0,
            "CArchive TOC entry contains an invalid size or offset",
        )
        end = position + entry_length
        _require(end <= len(toc_data), "CArchive TOC entry is truncated")
        raw_name = toc_data[position + reader_class._TOC_ENTRY_LENGTH : end]
        try:
            name = raw_name.rstrip(b"\0").decode("utf-8")
            typecode = raw_type.decode("ascii")
        except UnicodeDecodeError as exc:
            raise InventoryError("CArchive TOC encoding is invalid") from exc
        if typecode != "o":
            _require(name not in entries, f"duplicate CArchive member: {name}")
            entries[name] = (entry_offset, data_length, uncompressed_length, compression_flag, typecode)
        position = end
    _require(position == len(toc_data), "CArchive TOC parsing did not terminate exactly")
    return entries


def _classify_native(name: str, python_version: str) -> tuple[list[str], str, str]:
    base = PurePosixPath(name).name.casefold()
    observed = f"observed CArchive member {name}"
    if base == "python311.dll":
        return [observed, f"controlled build Python {python_version}"], "cpython", "identified"
    if base.startswith("vcruntime") or base.startswith("msvcp") or base.startswith("ucrtbase"):
        return [observed], "microsoft-vc-runtime", "partially-identified"
    if base in {"tcl86t.dll", "tk86t.dll", "_tkinter.pyd"}:
        return [observed], "tcl-tk", "partially-identified"
    if base in {"libcrypto-3.dll", "libssl-3.dll", "_ssl.pyd", "_hashlib.pyd"}:
        return [observed], "openssl", "partially-identified"
    if base in {"sqlite3.dll", "_sqlite3.pyd"}:
        return [observed], "sqlite", "partially-identified"
    if base in {"libffi-8.dll", "_ctypes.pyd"}:
        return [observed], "libffi", "partially-identified"
    if base in {"_bz2.pyd", "_decimal.pyd", "_lzma.pyd"}:
        return [observed], "unresolved-external-code-in-python-extension", "unresolved"
    if base.endswith(".pyd"):
        return [observed, f"controlled build Python {python_version}"], "cpython", "identified"
    return [observed], "unresolved-native-member", "unresolved"


def _component_records(
    native_members: list[dict[str, Any]], python_version: str, pyinstaller_version: str
) -> list[dict[str, Any]]:
    names = {record["name"] for record in native_members}
    by_mapping: dict[str, list[str]] = {}
    for record in native_members:
        by_mapping.setdefault(record["license_mapping"], []).append(record["name"])

    def observed(mapping: str) -> list[str]:
        return [f"observed CArchive member {name}" for name in sorted(by_mapping.get(mapping, []), key=str.casefold)]

    records = [
        {
            "id": "cpython",
            "name": "CPython runtime",
            "version": python_version,
            "evidence": sorted([f"controlled build Python {python_version}", *observed("cpython")], key=str.casefold),
            "license_files": ["legal/licenses/Python-3.11.9-LICENSE.txt"],
            "status": "identified",
        },
        {
            "id": "libffi",
            "name": "libffi runtime",
            "version": "unverified",
            "evidence": observed("libffi"),
            "license_files": [],
            "status": "partially-identified" if observed("libffi") else "unresolved",
        },
        {
            "id": "microsoft-vc-runtime",
            "name": "Microsoft Visual C runtime",
            "version": "unverified",
            "evidence": observed("microsoft-vc-runtime"),
            "license_files": [],
            "status": "partially-identified" if observed("microsoft-vc-runtime") else "unresolved",
        },
        {
            "id": "openssl",
            "name": "OpenSSL runtime",
            "version": "unverified",
            "evidence": observed("openssl"),
            "license_files": [],
            "status": "partially-identified" if observed("openssl") else "unresolved",
        },
        {
            "id": "pyinstaller-bootloader",
            "name": "PyInstaller bootloader",
            "version": pyinstaller_version,
            "evidence": sorted(
                ["outer PE contains a readable PyInstaller CArchive", f"controlled build PyInstaller {pyinstaller_version}"],
                key=str.casefold,
            ),
            "license_files": ["legal/licenses/PyInstaller-6.21.0-COPYING.txt"],
            "status": "identified",
        },
        {
            "id": "sqlite",
            "name": "SQLite runtime",
            "version": "unverified",
            "evidence": observed("sqlite"),
            "license_files": [],
            "status": "partially-identified" if observed("sqlite") else "unresolved",
        },
        {
            "id": "tcl-tk",
            "name": "Tcl/Tk runtime",
            "version": "unverified",
            "evidence": observed("tcl-tk"),
            "license_files": ["legal/licenses/Tcl-Tk-license.terms"],
            "status": "partially-identified" if observed("tcl-tk") else "unresolved",
        },
        {
            "id": "zlib",
            "name": "zlib dependency status",
            "version": "unverified",
            "evidence": [
                "no separately named zlib native CArchive member was observed; outer PE and operating-system dependencies were not resolved"
            ],
            "license_files": [],
            "status": "unresolved",
        },
    ]
    _require("python311.dll" in names, "CPython runtime member is missing")
    return sorted(records, key=lambda record: record["id"])


def _pe_fixed_file_version(path: Path) -> str | None:
    if os.name != "nt":
        return None

    class VSFixedFileInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint32) for name in (
            "signature", "struct_version", "file_version_ms", "file_version_ls",
            "product_version_ms", "product_version_ls", "file_flags_mask", "file_flags",
            "file_os", "file_type", "file_subtype", "file_date_ms", "file_date_ls",
        )]

    version_api = ctypes.windll.version
    handle = ctypes.c_uint32(0)
    size = version_api.GetFileVersionInfoSizeW(str(path), ctypes.byref(handle))
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not version_api.GetFileVersionInfoW(str(path), 0, size, buffer):
        return None
    pointer = ctypes.c_void_p()
    length = ctypes.c_uint32(0)
    if not version_api.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return None
    if length.value < ctypes.sizeof(VSFixedFileInfo):
        return None
    info = ctypes.cast(pointer, ctypes.POINTER(VSFixedFileInfo)).contents
    if info.signature != 0xFEEF04BD:
        return None
    parts = (
        info.file_version_ms >> 16,
        info.file_version_ms & 0xFFFF,
        info.file_version_ls >> 16,
        info.file_version_ls & 0xFFFF,
    )
    return ".".join(str(part) for part in parts)


def _verify_regular_file(path: Path) -> None:
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode), f"not a regular file: {path.name}")
    _require(not path.is_symlink(), f"symlink is not allowed: {path.name}")
    attributes = getattr(info, "st_file_attributes", 0)
    _require(not attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0), f"reparse point is not allowed: {path.name}")


def _verify_pe_x64(path: Path) -> None:
    with path.open("rb") as stream:
        header = stream.read(64)
        _require(len(header) == 64 and header[:2] == b"MZ", "MZ header is missing")
        pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
        stream.seek(pe_offset)
        signature = stream.read(6)
    _require(len(signature) == 6 and signature[:4] == b"PE\0\0", "PE signature is missing")
    _require(struct.unpack_from("<H", signature, 4)[0] == 0x8664, "PE machine is not x86-64")


def _native_kind(name: str) -> str | None:
    suffix = PurePosixPath(name).suffix.casefold()
    return {".dll": "dll", ".pyd": "pyd", ".exe": "exe"}.get(suffix)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _string_list(value: Any, *, sorted_required: bool, allow_empty: bool = False) -> bool:
    if not isinstance(value, list) or (not value and not allow_empty):
        return False
    if not all(isinstance(item, str) and item for item in value) or len(value) != len(set(value)):
        return False
    return not sorted_required or value == sorted(value, key=str.casefold)


def _verify_hygiene(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    _require(LOCAL_PATH_RE.search(serialized) is None, "inventory contains a local absolute path")
    _require(not any(pattern.search(serialized) for pattern in SECRET_RES), "inventory contains a secret-like value")
    folded = serialized.casefold()
    _require("timestamp" not in folded, "inventory contains a timestamp field or claim")
    for claim in FORBIDDEN_CLAIMS:
        _require(claim not in folded, f"inventory contains unsupported claim: {claim}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InventoryError(message)


if __name__ == "__main__":
    if hasattr(os.sys.stdout, "reconfigure"):
        os.sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
