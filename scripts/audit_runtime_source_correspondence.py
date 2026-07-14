from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import struct
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


FFMPEG_ARCHIVE_NAME = "ffmpeg-8.1.2-essentials_build.zip"
FFMPEG_ARCHIVE_SHA256 = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
FFMPEG_BINARY_HASHES = {
    "ffmpeg.exe": "1326dde4c84ff1f96fe6b8916c5bed29e163e9b5dccf995f6f3db069d143ec5e",
    "ffprobe.exe": "b49ccc7c6547b141ad5a2f6ec69cc04323d7133d7704d70b331b904c63eecb07",
}
FFMPEG_COMMIT = "38b88335f99e76ed89ff3c93f877fdefce736c13"
FFMPEG_SOURCE_ROOT = f"FFmpeg-{FFMPEG_COMMIT}"

ARIA2_ARCHIVE_NAME = "aria2-1.37.0-win-64bit-build1.zip"
ARIA2_ARCHIVE_SHA256 = "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288"
ARIA2_BINARY_HASHES = {
    "aria2c.exe": "be2099c214f63a3cb4954b09a0becd6e2e34660b886d4c898d260febfe9d70c2",
}
ARIA2_COMMIT = "02f2d0d8472b3c38c29b4dba8c75ebd5fdd2899a"
ARIA2_SOURCE_ROOT = f"aria2-{ARIA2_COMMIT}"

BASELINE_RE = re.compile(r"[0-9a-f]{40}\Z")
SOURCE_URL_RE = re.compile(r"https://github\.com/FFmpeg/FFmpeg/commit/([0-9a-f]+)")
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".tar.gz", ".tgz")
PE_LIMITATION = (
    "PE imports describe dynamic dependencies only and do not enumerate "
    "statically linked source components."
)

SYSTEM_DLLS = {
    "advapi32.dll",
    "avicap32.dll",
    "avrt.dll",
    "bcrypt.dll",
    "cabinet.dll",
    "comdlg32.dll",
    "crypt32.dll",
    "d3d11.dll",
    "d3d12.dll",
    "dxgi.dll",
    "gdi32.dll",
    "iphlpapi.dll",
    "kernel32.dll",
    "mf.dll",
    "mfplat.dll",
    "mfuuid.dll",
    "msimg32.dll",
    "msvcrt.dll",
    "ncrypt.dll",
    "ole32.dll",
    "oleaut32.dll",
    "psapi.dll",
    "secur32.dll",
    "shell32.dll",
    "shlwapi.dll",
    "user32.dll",
    "userenv.dll",
    "version.dll",
    "winmm.dll",
    "ws2_32.dll",
    "wsock32.dll",
}

FFMPEG_SYSTEM_COMPONENTS = {
    "d3d11va",
    "d3d12va",
    "dxva2",
    "mediafoundation",
    "vaapi",
}


class AuditError(AssertionError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit exact GPL runtime packages and core source archives without executing binaries"
    )
    parser.add_argument("--ffmpeg-archive", required=True, type=Path)
    parser.add_argument("--aria2-archive", required=True, type=Path)
    parser.add_argument("--ffmpeg-source", required=True, type=Path)
    parser.add_argument("--aria2-source", required=True, type=Path)
    parser.add_argument("--baseline-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        if BASELINE_RE.fullmatch(args.baseline_commit) is None:
            raise AuditError("baseline commit must be 40 lowercase hexadecimal characters")
        inputs = (
            args.ffmpeg_archive,
            args.aria2_archive,
            args.ffmpeg_source,
            args.aria2_source,
        )
        for path in inputs:
            _require_regular_file(path)

        document = create_audit(
            ffmpeg_archive=args.ffmpeg_archive,
            aria2_archive=args.aria2_archive,
            ffmpeg_source=args.ffmpeg_source,
            aria2_source=args.aria2_source,
            baseline_commit=args.baseline_commit,
        )
        output = args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(canonical_json_bytes(document))
    except (AuditError, OSError, UnicodeError, zipfile.BadZipFile, struct.error) as exc:
        print(f"runtime source correspondence audit failed: {exc}", file=sys.stderr)
        return 1

    print("runtime source correspondence audit created")
    return 0


def create_audit(
    *,
    ffmpeg_archive: Path,
    aria2_archive: Path,
    ffmpeg_source: Path,
    aria2_source: Path,
    baseline_commit: str,
) -> dict[str, Any]:
    ffmpeg_package = _audit_binary_package(
        ffmpeg_archive,
        expected_filename=FFMPEG_ARCHIVE_NAME,
        expected_archive_sha256=FFMPEG_ARCHIVE_SHA256,
        expected_binaries=FFMPEG_BINARY_HASHES,
        metadata_names=("README.txt", "LICENSE"),
    )
    aria2_package = _audit_binary_package(
        aria2_archive,
        expected_filename=ARIA2_ARCHIVE_NAME,
        expected_archive_sha256=ARIA2_ARCHIVE_SHA256,
        expected_binaries=ARIA2_BINARY_HASHES,
        metadata_names=(
            "AUTHORS",
            "ChangeLog",
            "COPYING",
            "LICENSE.OpenSSL",
            "NEWS",
            "README.html",
            "README.mingw",
        ),
    )
    ffmpeg_source_data = _audit_source_archive(
        ffmpeg_source,
        expected_root=FFMPEG_SOURCE_ROOT,
        license_name="COPYING.GPLv3",
    )
    aria2_source_data = _audit_source_archive(
        aria2_source,
        expected_root=ARIA2_SOURCE_ROOT,
        license_name="COPYING",
    )

    ffmpeg_readme = ffmpeg_package["metadata_text"]["README.txt"]
    aria2_readme = aria2_package["metadata_text"]["README.mingw"]
    aria2_changelog = aria2_package["metadata_text"]["ChangeLog"]

    ffmpeg_source_match = SOURCE_URL_RE.search(ffmpeg_readme)
    _require(ffmpeg_source_match is not None, "FFmpeg provider source reference is missing")
    source_abbreviation = ffmpeg_source_match.group(1)
    _require(
        len(source_abbreviation) >= 10 and FFMPEG_COMMIT.startswith(source_abbreviation),
        "FFmpeg provider source reference does not match the exact resolved commit",
    )
    _require(
        f"commit {ARIA2_COMMIT}" in aria2_changelog and "tag: release-1.37.0" in aria2_changelog,
        "aria2 package ChangeLog does not identify the exact source commit and release tag",
    )

    ffmpeg_external = _ffmpeg_external_components(ffmpeg_readme)
    aria2_external = _aria2_external_components(aria2_readme)
    packages = [
        {
            "id": "aria2",
            "binary_package": _public_binary_package(aria2_package),
            "distributed_binaries": aria2_package["distributed_binaries"],
            "provider": {
                "name": "aria2 project Windows release",
                "release_identity": "aria2 1.37.0 win-64bit-build1",
                "source_reference_evidence": [
                    f"ChangeLog identifies commit {ARIA2_COMMIT} and tag release-1.37.0",
                    "release archive name identifies release-1.37.0 win-64bit-build1",
                ],
                "configuration_evidence": [
                    "README.mingw identifies a statically linked mingw-w64 cross build on Ubuntu Linux",
                    "README.mingw identifies six linked libraries and their versions",
                ],
                "metadata_status": "verified",
            },
            "core_source": {
                "repository": "aria2/aria2",
                "commit": ARIA2_COMMIT,
                "archive_sha256": aria2_source_data["archive_sha256"],
                "license_path": "COPYING",
                "status": "core-source-identified",
            },
            "pe_imports": aria2_package["pe_imports"],
            "external_components": aria2_external,
            "build_recipe_status": "partial",
            "source_kit_status": "not-ready",
            "blockers": [
                "Exact compiler and toolchain versions are not identified by package metadata.",
                "Complete Windows build commands and patch or no-modification evidence are absent.",
                "Source archives for statically linked external components are not assembled.",
            ],
        },
        {
            "id": "ffmpeg",
            "binary_package": _public_binary_package(ffmpeg_package),
            "distributed_binaries": ffmpeg_package["distributed_binaries"],
            "provider": {
                "name": "Gyan FFmpeg builds",
                "release_identity": "8.1.2-essentials_build-www.gyan.dev",
                "source_reference_evidence": [
                    f"README.txt identifies upstream commit abbreviation {source_abbreviation}",
                    f"provider release page identifies source commit abbreviation {source_abbreviation}",
                    f"upstream resolution identifies exact commit {FFMPEG_COMMIT}",
                ],
                "configuration_evidence": [
                    "README.txt identifies a 64-bit static GPLv3 essentials build",
                    "README.txt records enabled build features and exact external-library names",
                    "README.txt does not provide a complete build command or external-library versions",
                ],
                "metadata_status": "partial",
            },
            "core_source": {
                "repository": "FFmpeg/FFmpeg",
                "commit": FFMPEG_COMMIT,
                "archive_sha256": ffmpeg_source_data["archive_sha256"],
                "license_path": "COPYING.GPLv3",
                "status": "core-source-identified",
            },
            "pe_imports": ffmpeg_package["pe_imports"],
            "external_components": ffmpeg_external,
            "build_recipe_status": "partial",
            "source_kit_status": "not-ready",
            "blockers": [
                "External-library versions and immutable source refs are not identified by package metadata.",
                "Complete provider build scripts, exact configure command, toolchain versions, and patch evidence are absent.",
                "Core FFmpeg source alone is not complete Corresponding Source for the static package.",
                "Source archives for evidenced non-system static components are not assembled.",
            ],
        },
    ]
    return {
        "schema_version": 1,
        "audit_scope": "pinned-distributed-gpl-runtime-packages",
        "baseline_commit": baseline_commit,
        "legal_compliance_certified": False,
        "corresponding_source_complete": False,
        "release_gate_status": "fail-closed",
        "packages": packages,
    }


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _require_regular_file(path: Path) -> None:
    path = Path(path)
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode) and not path.is_symlink(), "audit input must be a regular file")
    attributes = getattr(info, "st_file_attributes", 0)
    _require(not attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400), "reparse-point input is not allowed")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _audit_binary_package(
    path: Path,
    *,
    expected_filename: str,
    expected_archive_sha256: str,
    expected_binaries: dict[str, str],
    metadata_names: tuple[str, ...],
) -> dict[str, Any]:
    _require(path.name == expected_filename, f"unexpected archive filename: {path.name}")
    archive_sha256 = _sha256_file(path)
    _require(archive_sha256 == expected_archive_sha256, f"archive hash mismatch: {expected_filename}")
    with zipfile.ZipFile(path) as archive:
        members, manifest_hash = _audit_zip(archive)
        binary_entries: dict[str, zipfile.ZipInfo] = {}
        metadata_entries: dict[str, zipfile.ZipInfo] = {}
        for info in members:
            basename = PurePosixPath(info.filename).name
            if basename in expected_binaries:
                _require(basename not in binary_entries, f"duplicate binary name: {basename}")
                binary_entries[basename] = info
            if basename in metadata_names:
                _require(basename not in metadata_entries, f"duplicate metadata name: {basename}")
                metadata_entries[basename] = info
        _require(set(binary_entries) == set(expected_binaries), "required distributed binary set is incomplete")
        _require(set(metadata_entries) == set(metadata_names), "required provider metadata set is incomplete")

        distributed: list[dict[str, Any]] = []
        pe_imports: list[dict[str, Any]] = []
        for name in sorted(binary_entries, key=str.casefold):
            data = archive.read(binary_entries[name])
            digest = hashlib.sha256(data).hexdigest()
            _require(digest == expected_binaries[name], f"binary hash mismatch: {name}")
            pe = _parse_pe(data, name)
            distributed.append(
                {"name": name, "size": len(data), "sha256": digest, "machine": pe["machine"]}
            )
            pe_imports.append(
                {
                    "binary": name,
                    "machine": pe["machine"],
                    "pe_format": pe["pe_format"],
                    "dynamic_imports": pe["dynamic_imports"],
                    "delay_imports": pe["delay_imports"],
                    "duplicate_imports": pe["duplicate_imports"],
                    "system_dynamic_imports": pe["system_dynamic_imports"],
                    "non_system_dynamic_imports": pe["non_system_dynamic_imports"],
                    "limitation": PE_LIMITATION,
                }
            )

        metadata_records: list[dict[str, Any]] = []
        metadata_text: dict[str, str] = {}
        for name in sorted(metadata_entries, key=str.casefold):
            data = archive.read(metadata_entries[name])
            _require(not data.startswith((b"\xff\xfe", b"\xfe\xff")), f"unsupported metadata encoding: {name}")
            text = data.decode("utf-8-sig", errors="strict")
            _require("\0" not in text, f"metadata contains NUL: {name}")
            metadata_text[name] = text
            contains = _classify_metadata_text(text)
            metadata_records.append(
                {
                    "path": metadata_entries[name].filename,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "encoding": "utf-8",
                    "bom": "utf-8" if data.startswith(b"\xef\xbb\xbf") else "none",
                    "line_endings": _line_ending_form(data),
                    "contains": contains,
                }
            )
    return {
        "filename": expected_filename,
        "sha256": archive_sha256,
        "archive_manifest_sha256": manifest_hash,
        "provider_metadata_files": metadata_records,
        "metadata_text": metadata_text,
        "distributed_binaries": distributed,
        "pe_imports": pe_imports,
    }


def _public_binary_package(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "filename": package["filename"],
        "sha256": package["sha256"],
        "archive_manifest_sha256": package["archive_manifest_sha256"],
        "provider_metadata_files": package["provider_metadata_files"],
    }


def _audit_source_archive(path: Path, *, expected_root: str, license_name: str) -> dict[str, Any]:
    archive_sha256 = _sha256_file(path)
    with zipfile.ZipFile(path) as archive:
        members, manifest_hash = _audit_zip(archive)
        roots = {PurePosixPath(info.filename).parts[0] for info in members}
        _require(roots == {expected_root}, "source archive root does not identify the exact commit")
        license_path = f"{expected_root}/{license_name}"
        matches = [info for info in members if info.filename == license_path]
        _require(len(matches) == 1, f"source license is missing: {license_name}")
        license_data = archive.read(matches[0])
        _require(bool(license_data) and b"\0" not in license_data, "source license is empty or binary")
    return {
        "archive_sha256": archive_sha256,
        "archive_manifest_sha256": manifest_hash,
        "license_sha256": hashlib.sha256(license_data).hexdigest(),
    }


def _audit_zip(archive: zipfile.ZipFile) -> tuple[list[zipfile.ZipInfo], str]:
    _require(archive.testzip() is None, "ZIP integrity check failed")
    files: list[zipfile.ZipInfo] = []
    normalized: set[str] = set()
    all_paths: set[str] = set()
    file_paths: set[str] = set()
    manifest: list[dict[str, Any]] = []
    for info in archive.infolist():
        name = info.filename
        _require(name and "\\" not in name and "\0" not in name, "ZIP member path is invalid")
        pure = PurePosixPath(name)
        _require(not pure.is_absolute() and ".." not in pure.parts, "ZIP traversal member is not allowed")
        _require(not re.match(r"^[A-Za-z]:", name), "ZIP drive path is not allowed")
        canonical = "/".join(part for part in pure.parts if part not in ("", "."))
        _require(canonical, "empty ZIP member path is not allowed")
        collision_key = canonical.rstrip("/").casefold()
        _require(collision_key not in normalized, "duplicate or case-colliding ZIP member")
        normalized.add(collision_key)
        all_paths.add(canonical.rstrip("/"))
        mode = (info.external_attr >> 16) & 0o170000
        _require(mode != stat.S_IFLNK, "ZIP symbolic link is not allowed")
        _require(
            info.create_system != 0 or not info.external_attr & 0x400,
            "ZIP reparse-point representation is not allowed",
        )
        _require(not info.flag_bits & 0x1, "encrypted ZIP member is not allowed")
        if info.is_dir():
            continue
        lowered = canonical.casefold()
        _require(not lowered.endswith(ARCHIVE_SUFFIXES), "nested archive is not allowed")
        data = archive.read(info)
        files.append(info)
        file_paths.add(canonical)
        manifest.append(
            {
                "path": canonical,
                "size": info.file_size,
                "compressed_size": info.compress_size,
                "crc32": f"{info.CRC:08x}",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    for file_path in file_paths:
        _require(
            not any(other.startswith(file_path + "/") for other in all_paths),
            "ZIP file/directory path collision",
        )
    manifest.sort(key=lambda item: item["path"].casefold())
    digest = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    return files, digest


def _parse_pe(data: bytes, name: str) -> dict[str, Any]:
    _require(len(data) >= 0x100 and data[:2] == b"MZ", f"invalid PE DOS header: {name}")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    _require(pe_offset + 24 <= len(data) and data[pe_offset : pe_offset + 4] == b"PE\0\0", f"invalid PE signature: {name}")
    machine, section_count, _, _, _, optional_size, _ = struct.unpack_from("<HHIIIHH", data, pe_offset + 4)
    _require(machine == 0x8664, f"PE machine is not x86_64: {name}")
    optional = pe_offset + 24
    _require(optional + optional_size <= len(data), f"truncated PE optional header: {name}")
    _require(optional_size >= 112, f"PE optional header is too small: {name}")
    magic = struct.unpack_from("<H", data, optional)[0]
    _require(magic == 0x20B, f"PE is not PE32+: {name}")
    directory_count = struct.unpack_from("<I", data, optional + 108)[0]
    directories = optional + 112
    section_offset = optional + optional_size
    sections: list[tuple[int, int, int, int]] = []
    for index in range(section_count):
        offset = section_offset + index * 40
        _require(offset + 40 <= len(data), f"truncated PE section table: {name}")
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from("<IIII", data, offset + 8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_offset, raw_size))

    def rva_to_offset(rva: int) -> int:
        for virtual_address, span, raw_offset, raw_size in sections:
            if virtual_address <= rva < virtual_address + span:
                delta = rva - virtual_address
                _require(delta < raw_size, f"PE RVA points outside raw section: {name}")
                return raw_offset + delta
        _require(False, f"PE RVA cannot be mapped: {name}")
        return 0

    def read_c_string(rva: int) -> str:
        offset = rva_to_offset(rva)
        end = data.find(b"\0", offset, min(len(data), offset + 4096))
        _require(end >= 0, f"unterminated PE import name: {name}")
        try:
            return data[offset:end].decode("ascii").casefold()
        except UnicodeDecodeError as exc:
            raise AuditError(f"non-ASCII PE import name: {name}") from exc

    def directory(index: int) -> tuple[int, int]:
        if index >= directory_count:
            return (0, 0)
        _require(
            directories + (index + 1) * 8 <= optional + optional_size,
            f"PE data directory is truncated: {name}",
        )
        return struct.unpack_from("<II", data, directories + index * 8)

    imports: list[str] = []
    import_rva, import_size = directory(1)
    if import_rva and import_size:
        offset = rva_to_offset(import_rva)
        limit = min(len(data), offset + import_size)
        while offset + 20 <= limit:
            descriptor = struct.unpack_from("<IIIII", data, offset)
            if not any(descriptor):
                break
            imports.append(read_c_string(descriptor[3]))
            offset += 20

    delay_imports: list[str] = []
    delay_rva, delay_size = directory(13)
    if delay_rva and delay_size:
        offset = rva_to_offset(delay_rva)
        limit = min(len(data), offset + delay_size)
        image_base = struct.unpack_from("<Q", data, optional + 24)[0]
        while offset + 32 <= limit:
            descriptor = struct.unpack_from("<IIIIIIII", data, offset)
            if not any(descriptor):
                break
            attributes, name_value = descriptor[0], descriptor[1]
            name_rva = name_value if attributes & 1 else name_value - image_base
            _require(0 < name_rva <= 0xFFFFFFFF, f"invalid PE delay import name: {name}")
            delay_imports.append(read_c_string(name_rva))
            offset += 32

    duplicate_imports = sorted(
        {item for item in imports + delay_imports if (imports + delay_imports).count(item) > 1},
        key=str.casefold,
    )
    imports = sorted(set(imports), key=str.casefold)
    delay_imports = sorted(set(delay_imports), key=str.casefold)
    all_imports = set(imports + delay_imports)
    return {
        "machine": "x86_64",
        "pe_format": "PE32+",
        "dynamic_imports": imports,
        "delay_imports": delay_imports,
        "duplicate_imports": duplicate_imports,
        "system_dynamic_imports": sorted(
            [item for item in all_imports if _is_system_dll(item)],
            key=str.casefold,
        ),
        "non_system_dynamic_imports": sorted(
            [item for item in all_imports if not _is_system_dll(item)],
            key=str.casefold,
        ),
    }


def _is_system_dll(name: str) -> bool:
    lowered = name.casefold()
    return lowered in SYSTEM_DLLS or lowered.startswith(("api-ms-win-", "ext-ms-win-"))


def _ffmpeg_external_components(readme: str) -> list[dict[str, Any]]:
    names = _parse_table_section(readme, "External libraries:", "External libraries providing hardware acceleration:")
    names += _parse_table_section(readme, "External libraries providing hardware acceleration:", "Libraries:")
    _require(len(names) == len(set(names)) and names, "FFmpeg external component metadata is invalid")
    records = []
    for name in sorted(names, key=str.casefold):
        linkage = "system" if name in FFMPEG_SYSTEM_COMPONENTS else "static"
        source_status = "partial" if linkage == "system" else "unresolved"
        records.append(
            {
                "id": _component_id(name),
                "name": name,
                "version": "unverified",
                "evidence": [f"README.txt exact provider library list names {name}"],
                "linkage": linkage,
                "source_repository": "unverified",
                "source_ref": "unverified",
                "license_status": "unresolved",
                "source_status": source_status,
            }
        )
    return records


def _aria2_external_components(readme: str) -> list[dict[str, Any]]:
    expected = {
        "c-ares": "1.19.1",
        "expat": "2.5.0",
        "gmp": "6.3.0",
        "libssh2": "1.11.0",
        "sqlite": "3.43.1",
        "zlib": "1.3",
    }
    found = dict(re.findall(r"^\* ([a-zA-Z0-9-]+) ([0-9][^\s]*)$", readme, re.MULTILINE))
    _require(found == expected, "aria2 linked-library metadata differs from the expected package evidence")
    return [
        {
            "id": _component_id(name),
            "name": name,
            "version": version,
            "evidence": [f"README.mingw linked libraries list identifies {name} {version}"],
            "linkage": "static",
            "source_repository": "unverified",
            "source_ref": "unverified",
            "license_status": "unresolved",
            "source_status": "unresolved",
        }
        for name, version in sorted(expected.items())
    ]


def _parse_table_section(text: str, start: str, end: str) -> list[str]:
    _require(start in text and end in text, f"provider metadata section is missing: {start}")
    section = text.split(start, 1)[1].split(end, 1)[0]
    return re.findall(r"[a-z0-9_+.-]+", section.casefold())


def _component_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def _line_ending_form(data: bytes) -> str:
    crlf = data.count(b"\r\n")
    bare_cr = data.replace(b"\r\n", b"").count(b"\r")
    bare_lf = data.replace(b"\r\n", b"").count(b"\n")
    forms = sum(bool(value) for value in (crlf, bare_cr, bare_lf))
    if forms == 0:
        return "none"
    if forms > 1:
        return "mixed"
    if crlf:
        return "crlf"
    if bare_cr:
        return "cr"
    return "lf"


def _classify_metadata_text(text: str) -> dict[str, bool]:
    folded = text.casefold()
    return {
        "binary_version": bool(
            re.search(r"(?im)^version:|\btag: release-[0-9]", text)
        ),
        "source_reference": bool(
            re.search(r"(?i)(?:source code:|\bcommit [0-9a-f]{10,40}\b|tag: release-[0-9])", text)
        ),
        "configure_line": bool(re.search(r"(?im)^\s*(?:\./)?configure\b", text)),
        "external_library_versions": bool(
            re.search(r"(?m)^\* [a-zA-Z0-9-]+ [0-9][^\s]*$", text)
        ),
        "build_toolchain": any(
            token in folded for token in ("mingw", "toolchain", "nasm", "compiled using", "cross compiler")
        ),
        "build_date": bool(re.search(r"(?im)^\s*build date\s*:", text)),
        "license_label": bool(
            re.search(r"(?im)^\s*(?:license:|gnu general public license|apache license|openssl license)", text)
        ),
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
