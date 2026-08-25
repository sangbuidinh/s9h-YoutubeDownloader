from __future__ import annotations

import argparse
import hashlib
import io
import json
import lzma
import sys
import tarfile
import zipfile
from pathlib import Path

import source_compliance as compliance


ARIA2_BUILD_FILES = (
    "aria2-1.37.0/Dockerfile.mingw",
    "aria2-1.37.0/mingw-build-memo",
    "aria2-1.37.0/mingw-config",
    "aria2-1.37.0/mingw-release",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or verify a deterministic source-compliance asset")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--owner", required=True, type=Path)
    create.add_argument("--downloads-root", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--license", required=True, type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--owner", required=True, type=Path)
    verify.add_argument("--package", required=True, choices=("aria2", "ffmpeg"))
    verify.add_argument("--asset", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            owner = compliance.load_owner(args.owner, allow_unsealed_asset=True)
            result = create_aria2_source_kit(owner, args.downloads_root, args.output, args.license)
            print(json.dumps(result, sort_keys=True))
        else:
            owner = compliance.load_owner(args.owner)
            manifest = compliance.verify_source_asset(owner, args.package, args.asset)
            print(f"{args.package} source asset verified: {len(manifest['files']) + 1} entries")
    except (compliance.SourceComplianceError, OSError, UnicodeError, json.JSONDecodeError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"Source kit error: {exc}", file=sys.stderr)
        return 1
    return 0


def create_aria2_source_kit(owner: dict, downloads_root: Path, output: Path, license_path: Path) -> dict[str, object]:
    compliance.validate_owner(owner, allow_unsealed_asset=True)
    kit = next(item for item in owner["kits"] if item["id"] == "aria2")
    if kit["status"] != "ready":
        raise compliance.SourceComplianceError("aria2 source kit is not marked ready")
    downloads_root = downloads_root.resolve(strict=True)
    files: dict[str, tuple[bytes, str]] = {}
    for identity in kit["identities"]:
        source = downloads_root / identity["archive_filename"]
        if not source.is_file():
            raise compliance.SourceComplianceError(f"source archive is unavailable: {identity['component_id']}")
        data = source.read_bytes()
        if len(data) != identity["archive_size"] or hashlib.sha256(data).hexdigest() != identity["archive_sha256"]:
            raise compliance.SourceComplianceError(f"source archive identity mismatch: {identity['component_id']}")
        files[f"sources/{identity['archive_filename']}"] = (data, "source-archive")
    aria2_identity = next(item for item in kit["identities"] if item["component_id"] == "aria2")
    aria2_archive = downloads_root / aria2_identity["archive_filename"]
    with lzma.open(aria2_archive, "rb") as stream:
        with tarfile.open(fileobj=stream, mode="r:") as archive:
            members = {member.name: member for member in archive.getmembers()}
            for source_name in ARIA2_BUILD_FILES:
                member = members.get(source_name)
                if member is None or not member.isfile():
                    raise compliance.SourceComplianceError(f"aria2 build script is missing: {source_name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise compliance.SourceComplianceError(f"aria2 build script is unreadable: {source_name}")
                files[f"build/{Path(source_name).name}"] = (extracted.read(), "build-script")
    license_data = license_path.read_bytes()
    if not license_data:
        raise compliance.SourceComplianceError("aria2 license is empty")
    files["licenses/aria2-1.37.0-GPLv2.txt"] = (license_data, "license")
    notice = (
        "Youtube Downloaderbs v1.3.2 aria2 source asset\n"
        "\n"
        "This deterministic archive maps the distributed aria2 Windows package to the\n"
        "authoritative aria2 1.37.0 source release, its six exact external source\n"
        "archives, and the package-specific MinGW build scripts contained in that\n"
        "release. General-purpose compiler binaries are not embedded.\n"
        "\n"
        "This is technical source-compliance evidence, not legal advice and not a\n"
        "claim of a byte-identical or reproducible rebuild.\n"
    ).encode("utf-8")
    files["NOTICE.txt"] = (notice, "notice")
    records = [
        {
            "name": name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "role": role,
        }
        for name, (data, role) in sorted(files.items())
    ]
    manifest = {
        "schema_version": 1,
        "package_id": "aria2",
        "release_tag": owner["release_tag"],
        "binary_package": kit["binary_package"],
        "source_identities": kit["identities"],
        "files": records,
        "non_claims": ["byte-identical-rebuild", "legal-advice", "reproducible-build"],
    }
    manifest_bytes = compliance.canonical_json_bytes(manifest)
    all_files = {"SOURCE_MANIFEST.json": manifest_bytes, **{name: data for name, (data, _) in files.items()}}
    output.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(all_files.items()):
            info = zipfile.ZipInfo(name, compliance.FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = compliance.FIXED_FILE_MODE
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    output.write_bytes(buffer.getvalue())
    return {
        "filename": output.name,
        "size": output.stat().st_size,
        "sha256": compliance.sha256_file(output),
        "entry_count": len(all_files),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
